# photoAWS — 照片处理 Pipeline

端到端的图像处理流水线：**用户上传照片 → InsightFace 人脸检测 + 512 维 embedding → 打标 → VLM 结构化提取**。AWS Step Functions 编排，本地 MiniStack 模拟 AWS，Terraform/OpenTofu 同一份代码跑本地 + 真 AWS，Langfuse 全链路分布式 trace。

---

## 核心设计

| 维度 | 选择 | 为什么 |
|---|---|---|
| **IaC** | OpenTofu + 2 workspace + 2 tfvars | 一份代码同时覆盖本地 MiniStack 和 AWS；state 隔离防止误操作 |
| **编排** | AWS Step Functions（ASL JSON） | 声明式状态机，原生 Lambda/Batch 集成，Map 并发、Retry、重试全内建 |
| **重计算** | AWS Batch + GPU EC2 (`g4dn.xlarge`) | `min_vcpus=0` 空闲缩零、有任务自动起 GPU 实例；比 ECS/Fargate 更适合间歇 batch |
| **轻 I/O** | 5 个 Lambda（Zip 部署） | 快速冷启动、按调用付费、0 运维；跨平台 wheel 用 `uv pip install --python-platform` |
| **数据库** | Supabase Postgres + pgvector (HNSW) | 托管 Postgres + 向量索引一站式，用 Transaction pooler（6543）避连接数问题 |
| **可观测** | Langfuse v4 OTel-based SDK | 真分布式 trace：Scheduler 是 root span，下游 Lambda/Worker 用 magic kwarg 真父子嵌套 |
| **本地开发** | MiniStack（LocalStack 替代，免费 MIT） | 25+ AWS 服务模拟；Batch / Activity runtime 不支持但有 workaround |
| **DX** | 国内镜像（tuna/BFSU）、模型预下载 | 避免每次 rebuild 从 GitHub 拉 300MB；apt/pip 也都走国内 |

---

## 架构

```
┌────────────────────────────────────────────────────────────────────┐
│ 用户 / API Gateway / 定时触发                                       │
└────────────────────────────────────────────────────────────────────┘
                                │
                                ▼ lambda.invoke
                    ┌───────────────────────┐
                    │  photo-scheduler      │  @observe(name="photo_pipeline") — root span
                    │  (Lambda, Python 3.12)│  propagate_attributes(user_id, session_id, tags)
                    │                       │  sfn.start_execution(input={trace_id, parent_obs_id, ...})
                    └───────────┬───────────┘
                                │
                                ▼ states.StartExecution
┌───────────────────────────────────────────────────────────────────┐
│  Step Functions: photo-pipeline                                   │
│                                                                   │
│    ┌─────────────────┐                                            │
│    │  RunWorker      │  batch:submitJob.sync (AWS) /              │
│    │                 │  ecs:runTask.sync (local)                  │
│    │                 │  → GPU EC2 拉 ECR image → InsightFace     │
│    │                 │    写 face_embeddings 表（pgvector）        │
│    └────────┬────────┘                                            │
│             ▼                                                     │
│    ┌─────────────────┐                                            │
│    │  GetPhotoIds    │  lambda:invoke                             │
│    │                 │  → SELECT photo_id FROM photos            │
│    └────────┬────────┘                                            │
│             ▼                                                     │
│    ┌─────────────────┐                                            │
│    │  TagPhotos Map  │  MaxConcurrency=10                         │
│    │     ├─ photo-tagger × N (并发)                              │
│    │     │  @observe("stage2_tag_photo")                         │
│    └────────┬────────┘                                            │
│             ▼                                                     │
│    ┌─────────────────┐                                            │
│    │  VLMExtract Map │  MaxConcurrency=10                         │
│    │     ├─ photo-vlm × N (并发)                                 │
│    │     │  @observe("stage3_vlm_extract")                       │
│    └────────┬────────┘                                            │
│             ▼                                                     │
│    ┌─────────────────┐                                            │
│    │  MarkComplete   │  batch.status = 'completed'               │
│    └─────────────────┘                                            │
└───────────────────────────────────────────────────────────────────┘

          外部集成                  存储                  可观测
      ┌──────────────┐        ┌──────────────┐       ┌──────────────┐
      │ S3 uploads   │◄───    │ Postgres +   │       │ Langfuse UI  │
      │ (fixtures)   │        │ pgvector     │       │ (分布式 trace) │
      └──────────────┘        │ (Supabase)   │       └──────────────┘
                              └──────────────┘
```

## 请求生命周期

一次完整的 `invoke photo-scheduler`：

1. **Scheduler Lambda** 收到 `{request_id, user_id, s3_keys}`
2. `create_batch()` 写入 Supabase `photo_batches` + N 条 `photos`（status=pending）
3. Scheduler 生成确定性 trace_id (`create_trace_id(seed=request_id)`)
4. `propagate_attributes(user_id, session_id=batch-{id}, tags)` 挂上 trace 级属性
5. `@observe("photo_pipeline")` 创建 root span，捕获自己 `observation_id`
6. `sfn.start_execution(input={trace_id, parent_obs_id, batch_id, s3_keys})` 触发状态机
7. **RunWorker**（AWS Batch / 本地 `docker compose run worker`）拉取 S3 图 → InsightFace buffalo_l 推理 → `face_embeddings` + 更新 `photos.status=completed` + `photos.face_count`
8. **GetPhotoIds** 查出 batch 的 photo_id 数组
9. **TagPhotos Map × N** 并发打 mock 标签（`photos.tags` JSONB）
10. **VLMExtract Map × N** 并发 VLM mock 提取（`photos.vlm_result` JSONB）
11. **MarkComplete** 置 `photo_batches.status='completed'`
12. 每个 Lambda 都通过 `@observe` 的 `langfuse_trace_id` + `langfuse_parent_observation_id` kwarg 把 span 挂到 root 下，构成**一棵完整 trace**

---

## Quick Start

```bash
# 1. 装依赖（macOS）
make deps          # brew install dbmate opentofu uv（docker 自己装 OrbStack/Docker Desktop）

# 2. 一键本地部署
make setup         # check-deps → docker compose up → migrate → build 6 images → tofu apply (local)

# 3. 跑端到端
make test-e2e      # 上传 fixtures → Scheduler Lambda → SFN → Worker → DB 校验

# 4. 清理
make destroy       # tofu destroy + docker compose down -v
```

首次运行会自动：
- 从 `.env.example` 复制 `.env`
- 下载 InsightFace buffalo_l 模型（275MB）到 `docker/models/`
- 构建 6 个 Docker 镜像（MiniStack + 5 Lambda + Worker）
- Terraform 部署 12 个本地资源

## Makefile 命令（按场景）

### 依赖 / 环境
| 命令 | 作用 |
|---|---|
| `make check-deps` | 校验 docker/uv/dbmate/tofu + docker 守护进程 + .env（`setup` 前置） |
| `make deps` | `brew install dbmate opentofu uv` |
| `make download-models` | 下载 buffalo_l 模型（幂等，已存在跳过） |

### 本地 MiniStack
| 命令 | 作用 |
|---|---|
| `make up` / `make down` | 启停 docker compose（MiniStack + PostgreSQL） |
| `make migrate` | dbmate 应用数据库迁移 |
| `make build-all` | 构建全部 6 个 Docker 镜像 |
| `make setup` | 一键本地全栈（上述四者 + tofu apply local.tfvars） |
| `make destroy` | 本地 tofu destroy + compose down -v |

### 测试
| 命令 | 作用 |
|---|---|
| `make test` | `uv run pytest tests/` — 33 单元测试 |
| `make test-e2e` | 端到端：Scheduler Lambda → SFN → docker worker → DB 校验 + Langfuse trace URL |

### AWS 部署
| 命令 | 作用 |
|---|---|
| `make build-push-worker-ecr` | Build + push Worker image 到 ECR（依赖 aws CLI 已登录） |
| `make apply-aws` | 真 AWS 部署（读 `.env` 的 Supabase + Langfuse 凭证组合 `-var` 传递） |
| `make destroy-aws` | 真 AWS 销毁 |

### Lint / Format
```bash
uv run ruff check .
uv run ruff format --check .
```

---

## IaC 结构

一份 `terraform/*.tf` 代码 + 两个 tfvars 覆盖两种目标：

```
terraform/
├── main.tf         Provider 配置 + locals（lambda_env）
├── variables.tf    所有 input variables 定义
├── lambda.tf       5 Lambda + Execution Role + 权限
├── batch.tf        AWS Batch CE/Queue/JobDef + IAM（count = is_local ? 0 : 1，仅 AWS）
├── ecs.tf          本地 ECS Task Definition（SFN runTask 目标，仅 local）
├── sfn.tf          Step Functions 状态机（local 用 create_sfn.sh 绕 MiniStack API，aws 用 aws_sfn_state_machine）
├── s3.tf           S3 bucket
├── sqs.tf          DLQ（可选）
├── outputs.tf      lambda_functions / sfn_arn / s3_bucket 等
├── local.tfvars    本地 MiniStack 配置
├── aws.tfvars      真 AWS 配置（DB URL 是占位符，真值由 Makefile `-var` 覆盖）
├── build_lambda_zip.sh  Lambda zip 打包（跨平台 wheel + 剥离 boto3）
└── create_sfn.sh   本地 SFN 创建脚本（绕 MiniStack `ListStateMachineVersions` 不支持）
```

### Workspace 隔离

本项目用 tofu workspace 隔离本地和 AWS 的 state：

```bash
tofu workspace list
# * default   ← 本地 MiniStack 用
#   aws      ← 真 AWS 用
```

- **本地** `make setup` 默认在 `default` workspace，state 指向 MiniStack
- **AWS** `make apply-aws` 自动切到 `aws` workspace（不存在则创建），state 完全独立

### 变量流

真 secret **不入 tfvars**，由 Makefile 读 `.env` 后通过 `-var` 命令行覆盖：

```
.env
  LANGFUSE_PUBLIC_KEY=pk-lf-...
  LANGFUSE_SECRET_KEY=sk-lf-...
  SUPABASE_DB_PASSWORD=...
           ▼
Makefile apply-aws target
  组装 DB URL + -var="lambda_database_url=..." -var="langfuse_secret_key=..."
           ▼
tofu apply -var-file=aws.tfvars -var="xxx=yyy"
  tfvars 提供非敏感默认（region、instance_type），-var 覆盖敏感字段
           ▼
Lambda 运行时 environment.variables 注入
```

这样：
- `terraform/aws.tfvars` 可以入库（所有字段都是占位符或非敏感值）
- `.env` 留在本地（`.gitignore`）
- CI/CD 场景可以用 secrets manager 取代 `.env`，sed/jq 组装同样的 `-var` 参数

---

## AWS 部署 Runbook

### 前置准备

1. **AWS 账户 + CLI 登录**
   ```bash
   brew install awscli
   aws login                          # 或 aws configure
   aws sts get-caller-identity        # 确认身份
   aws configure set region us-west-1 # 默认区域
   ```

2. **申请 GPU 配额**（新账号默认 0 vCPU）
   AWS Console → Service Quotas → EC2 → "Running On-Demand G and VT instances" → Request increase to ≥4
   ```bash
   # 或 CLI 申请：
   aws service-quotas request-service-quota-increase \
     --service-code ec2 --quota-code L-DB2E81BA --desired-value 4 --region us-west-1
   ```
   审批通常几小时到 1-2 天。

3. **Supabase**（托管 Postgres）
   - 建项目 → 运行迁移：
     ```bash
     dbmate --url "postgresql://postgres.<ref>:<pass>@aws-1-us-west-1.pooler.supabase.com:6543/postgres?sslmode=require" \
            -d ./migrations up
     ```
   - 记下 `<project-ref>` 和 DB password

4. **ECR repo**（手工建，不放 terraform，避免 destroy 删镜像）
   ```bash
   aws ecr create-repository --repository-name photo-worker --region us-west-1
   ```

5. **填 `.env`**
   ```bash
   # 追加到 .env
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   LANGFUSE_HOST=https://us.cloud.langfuse.com
   SUPABASE_PROJECT_REF=ubzbcncozrtgscocjsnm
   SUPABASE_DB_PASSWORD=...
   ```

### 部署步骤

```bash
# 1. Worker 镜像到 ECR
AWS_REGION=us-west-1 make build-push-worker-ecr

# 2. 应用基础设施（创建 ~20 资源）
AWS_REGION=us-west-1 make apply-aws

# 3. 上传测试照片到 S3
aws s3 cp tests/fixtures/face_0.jpg s3://photo-uploads-<account-id>-us-west-1/test/face_0.jpg

# 4. 真 smoke — invoke Scheduler Lambda
aws lambda invoke \
  --function-name photo-scheduler --region us-west-1 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"request_id":"smoke-1","user_id":1,"s3_keys":["test/face_0.jpg"]}' \
  /tmp/resp.json
cat /tmp/resp.json
# → {"batch_id": N, "execution_arn": "...", "langfuse_trace_id": "..."}

# 5. 看 SFN 进度
aws stepfunctions describe-execution --execution-arn <arn> --region us-west-1

# 6. 看 Langfuse trace
#   https://us.cloud.langfuse.com/trace/<langfuse_trace_id>

# 7. 清理
AWS_REGION=us-west-1 make destroy-aws
```

### 成本估算（us-west-1，跑一次 3 张照片 smoke）

| 资源 | 空闲成本 | 跑一次 |
|---|---|---|
| 5 Lambda | $0（按调用） | <$0.01 |
| Step Functions | $0（按 state transition） | <$0.01 |
| S3 | $0.02/GB/月 | 忽略 |
| Batch CE (`g4dn.xlarge`, `min_vcpus=0`) | **$0** | ~$0.50（启 + 跑 1-2 min + 停） |
| ECR | $0.1/GB/月 | 忽略 |
| CloudWatch Logs | 几 cent | 忽略 |
| **总计** | $0 | ~$0.5 |

`make destroy-aws` 后日常成本 $0（只剩 S3 空文件 + ECR 镜像）。

---

## 可观测性

### 一张 trace 看全链路

Langfuse UI 里一次 `photo-scheduler` invoke 产生：

```
Trace: photo_pipeline   (userId=<user>, sessionId=batch-42, tags=[photo-pipeline])
├─ photo_pipeline      ← Scheduler @observe root
│  input:  {request_id, user_id, s3_keys}
│  output: {batch_id, execution_arn, langfuse_trace_id}
│
├─ get_photo_ids
│  input:  {batch_id}
│  output: {batch_id, photo_ids}
│
├─ stage2_tag_photo × N（并行）
│  input:    {photo_id, s3_key}        ← metadata 里有业务字段方便筛选
│  output:   {photo_id, status, s3_key, tag_count}
│  metadata: {s3_key, tag_count}
│
├─ stage3_vlm_extract × N（并行）
│  ...
│
└─ mark_complete
   input:  {batch_id}
   output: {batch_id, status}
```

Worker 的 `stage1_face_detect` + N×`detect_one_photo` 子 span 也在同一 trace 下（共享 trace_id）。

### 传播机制

Langfuse v4 基于 OpenTelemetry，本项目用两种 magic kwarg 透传 trace 上下文：

```python
@observe(name="stage2_tag_photo")
def _tag_photo(photo_id): ...

# 下游 Lambda 这样调
_tag_photo(
    photo_id,
    langfuse_trace_id="<32-hex>",              # 同棵树
    langfuse_parent_observation_id="<16-hex>", # 真父子嵌套（不是 sibling 平铺）
)
```

通过 **SFN JSON 的 `$$.Execution.Input.xxx`** 从原始 input 直接取这两字段（不依赖上游 state 的 ResultPath 保留）：

```json
"Payload": {
  "photo_id.$": "$",
  "langfuse_trace_id.$": "$$.Execution.Input.langfuse_trace_id",
  "langfuse_parent_observation_id.$": "$$.Execution.Input.langfuse_parent_observation_id"
}
```

### 本地 / AWS 切换

| 环境 | Langfuse key 来源 |
|---|---|
| 本地 | `.env` → `make setup` 自动注入 Lambda env + docker compose worker env |
| AWS | `.env` → `make apply-aws` 通过 `-var` 注入 Terraform |

不设 Langfuse key 时 SDK 自动 no-op（空 trace_id），业务代码无改动。

---

## 项目结构

```
packages/common/                共享（DB / BatchManager / Tracing / Config）
└── src/common/tracing.py       init_trace_id / traced_handler / kwargs_from_event / run_traced

services/                       6 个服务（Python 3.12 + uv workspace）
├── scheduler/                  Lambda：创建 batch → propagate_attributes → start_execution
├── worker/                     Batch Job：InsightFace GPU 推理 + embedding 写入
├── get_photo_ids/              Lambda：查 batch 的 photo_ids
├── tagger/                     Lambda：照片打标（Stage 2，mock，@observe）
├── vlm_extractor/              Lambda：VLM 提取（Stage 3，mock，@observe）
└── mark_complete/              Lambda：batch 置完成

state-machines/
├── pipeline-local.json         ECS runTask.sync（本地 MiniStack）
└── pipeline-aws.json           Batch submitJob.sync（真 AWS；`${GPU_JOB_QUEUE}` 由 templatefile 替换）

terraform/                      IaC（见「IaC 结构」段）

tests/                          pytest 33 个用例
├── test_scheduler.py / test_tagger.py / ... 各 service 单测
├── test_tracing.py             新增 14 个 tracing 抽象测试
└── conftest.py                 shared fixtures

migrations/                     dbmate（photo_batches / photos / face_embeddings / HNSW 索引）
scripts/test_e2e.py             端到端脚本
docker/
├── ministack.Dockerfile        自定义 MiniStack 镜像（Alpine ensurepip 装 psycopg2/boto3/langfuse）
└── models/buffalo_l/           InsightFace 模型（.gitignore，make download-models 预下载）

docs/superpowers/specs/         设计文档归档
├── 2026-04-20-langsmith-to-langfuse-migration-design.md
├── 2026-04-20-sfn-activity-e2e-design.md
└── 2026-04-21-aws-batch-integration-design.md
```

---

## 测试

### 单元测试（33 passed）
```bash
make test                                  # 全跑
uv run pytest tests/test_scheduler.py -v   # 单个文件
```

覆盖：
- Scheduler handler（create_batch + start_execution + trace 字段断言）
- Tagger/VLM/GetPhotoIds/MarkComplete/Worker 各自 handler
- BatchManager（幂等、embedding 插入、状态转换）
- `common.tracing`（4 个公共符号的 14 个 case）
- Config（is_local / get_database_url）

### 端到端（make test-e2e）
```bash
make test-e2e
```

流程：
1. 上传 S3 fixtures（3 张测试人脸）
2. `lam.invoke('photo-scheduler', ...)` 真调 Scheduler Lambda
3. `docker compose run --rm worker` 让 Worker 跑 InsightFace（MiniStack ECS `runTask` 是假的）
4. `ecs.stop_task` 解锁 SFN 的 `.sync` 等待（MiniStack workaround）
5. 轮询 `describe_execution` → SUCCEEDED
6. 从 Supabase 验证：`photos.status='completed'` / `tags IS NOT NULL` / `vlm_result IS NOT NULL` / `face_embeddings` 有 3 条
7. 打印 Langfuse trace URL（手动眼验）

---

## 已知限制

### MiniStack

- **`ecs:runTask.sync` 不真跑容器**：AWS 侧 task 注册为 RUNNING 但不会自动 STOPPED，SFN 无限 block
  → e2e 脚本 `start_execution` 后主动 `ecs.stop_task`（`scripts/test_e2e.py:_unlock_ministack_ecs`）
  → Worker 改由 `docker compose run --rm worker` 自行调度
- **SFN Activity runtime 不调度**：`create_activity` 等 CRUD API 可用，但状态机挂 Activity 时 `get_activity_task` 恒返回空 token
  → 本地不用 Activity 模式（见 spec `2026-04-20-sfn-activity-e2e-design.md`）
- **`ListStateMachineVersions` API 不支持**：Terraform aws_sfn_state_machine provider 内部调用，失败
  → 本地用 `terraform_data` + `create_sfn.sh` 脚本直接 `create_state_machine`
- **Lambda Zip 部署的 Warm Worker** 缺 psycopg2/boto3/langfuse
  → 自定义 `docker/ministack.Dockerfile` 预装

### AWS 部署 DX

- **`aws login`（CLI v2.34+）的新 cache 在 `~/.aws/login/cache/`**，Terraform provider 不认
  → 用 `aws configure export-credentials --format env` 导成旧格式到 `~/.aws/credentials`
- **`.env` 的 `AWS_ENDPOINT_URL=http://localhost:4566` 污染真 AWS CLI**
  → Makefile 的 `apply-aws` / `destroy-aws` 用 `env -u` 清掉
- **AWS Batch Compute Environment 的 `desired_vcpus` 是 AWS 自动管理**，不能手动 scale-down
  → `terraform/batch.tf` 加 `lifecycle.ignore_changes = [compute_resources[0].desired_vcpus]`
- **GPU 配额新账户默认为 0**，需提 case 申请
- **S3 bucket 名全球唯一**，默认 `photo-uploads-<account_id>-<region>` 避免冲突

### Lambda zip

- **boto3/botocore 被排除**（省 20MB）：Lambda runtime 自带；若遇兼容性问题恢复即可
- **Python 3.12 跨平台 wheel**：uv 用 `--python-version 3.12 --python-platform x86_64-manylinux2014` 装
- **`*.dist-info` 必须保留**：OpenTelemetry / Langfuse 靠 `entry_points` 发现插件，删掉运行时 `StopIteration`

---

## Follow-ups / 已知技术债

- **Lambda IAM scope 收窄**：现在 5 个 Lambda 共用一个 role，`states:StartExecution` 只有 Scheduler 需要；可以拆成 scheduler-role / downstream-role（非阻塞，留 follow-up PR）
- **Lambda Layers**：langfuse + opentelemetry 在 5 个 zip 里重复（每个 ~10MB），移到共享 Layer 可以省 40MB
- **`as_type="generation"`**：VLM 当前是 mock，真接入 OpenAI/Anthropic 后改成 `@observe(as_type="generation")` + `update_current_generation(model, usage_details, ...)` 解锁 Langfuse UI 的 token / 成本面板
- **Worker 长驻化**：现在每次 batch 都冷启 Worker（载 InsightFace 模型 ~1-2s）；考虑 SageMaker Async Inference 或 Batch Compute 保 1 台暖机
- **Tofu remote backend**：本地 state 用 S3 backend + DynamoDB lock 更适合多人协作

---

## 技术栈

Python 3.12 · uv workspace monorepo · OpenTofu · MiniStack · Supabase Postgres + pgvector · InsightFace (ONNX) · AWS Batch · AWS Step Functions · AWS Lambda · Langfuse v4 SDK · OpenTelemetry

## CI

GitHub Actions：push / PR → Ruff lint + format → dbmate migrate（pgvector service container）→ pytest

---

## License / Credits

内部学习项目，参考：
- [MiniStack](https://github.com/Nahuel990/ministack) — LocalStack 替代，MIT
- [InsightFace](https://github.com/deepinsight/insightface) — 人脸检测 / embedding，buffalo_l 模型
- [Langfuse](https://langfuse.com) — LLM 可观测平台

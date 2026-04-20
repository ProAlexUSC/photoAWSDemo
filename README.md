# photoAWS — 照片处理 Pipeline

基于 AWS 的多阶段照片处理系统：人脸检测 → 打标 → VLM 结构化提取。

## 架构

Step Functions 编排完整 Pipeline：

```
Scheduler → Step Functions 状态机
  ├── RunWorker (ECS/Batch) — InsightFace 人脸检测 + embedding
  ├── GetPhotoIds (Lambda) — 查询 photo_ids
  ├── TagPhotos (Map, 并行) — 每张照片打标
  ├── VLMExtract (Map, 并行) — 每张照片 VLM 提取
  └── MarkComplete (Lambda) — 标记 batch 完成
```

本地用 MiniStack 模拟 AWS 服务，Terraform/OpenTofu 统一管理本地和 AWS 部署。

## 本地开发

```bash
make deps                     # 一次性：brew 装 dbmate opentofu uv（docker 自己装 OrbStack/Docker Desktop）
make setup                    # check-deps + up + migrate + build-all + tofu apply
make test-e2e                 # 端到端真跑 SFN（依赖 setup）
make destroy                  # tofu destroy + docker compose down -v
```

`.env` 缺失时 `check-deps` 会自动从 `.env.example` 复制；想自定义（如 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`）先手动 `cp .env.example .env` 再改。

### Makefile 完整命令

| 命令 | 说明 |
|------|------|
| `make check-deps` | 校验 docker/uv/dbmate/tofu + docker 守护进程 + .env（`setup` 前自动运行） |
| `make deps` | brew 安装 dbmate + opentofu + uv |
| `make download-models` | 预下载 InsightFace buffalo_l（~275MB）到 `docker/models/`（幂等，build-worker 自动触发） |
| `make up` | 启动 Docker Compose（MiniStack + PostgreSQL） |
| `make down` | 停止 Docker Compose |
| `make migrate` | 执行 dbmate 数据库迁移 |
| `make build-all` | 构建全部 6 个 Docker 镜像 |
| `make setup` | `check-deps` + `up` + `migrate` + `build-all` + `tofu apply`（一键启动） |
| `make destroy` | `tofu destroy` + `docker compose down -v`（一键销毁） |
| `make test` | 运行单元测试 |
| `make test-e2e` | 端到端：Worker + 真 SFN + DB 校验（依赖 `setup`） |

### 前置依赖

- Docker（OrbStack 或 Docker Desktop）
- [uv](https://docs.astral.sh/uv/)（Python 3.12 由 uv 管理）
- [dbmate](https://github.com/amacneil/dbmate)
- [OpenTofu](https://opentofu.org/)（或 Terraform）

macOS 上 `make deps` 一键装后三项。缺哪个 `make check-deps` 会明确报。

### 国内镜像

项目默认已配置：Debian apt → 清华 tuna；PyPI（ministack + uv）→ BFSU；InsightFace buffalo_l → 主机预下载 + `COPY` 进 worker 镜像（避免每次 rebuild 都从 GitHub 拉 300MB）。

## 部署

同一套 Terraform 配置，通过 `tfvars` 切换环境：

```bash
# 本地 MiniStack（make setup 已包含）
cd terraform && tofu apply -var-file=local.tfvars

# AWS 部署（填写 Supabase 连接串、ECR 镜像等）
cd terraform && tofu apply -var-file=aws.tfvars

# 销毁 AWS 资源（防扣费）
cd terraform && tofu destroy -var-file=aws.tfvars
```

## 可观测性

| 层 | 工具 | 说明 |
|----|------|------|
| Pipeline 全局 | Step Functions 执行历史 | 每步耗时/状态（AWS Console） |
| Lambda 业务 | Langfuse v4 `@observe` | 分布式 trace 真父子嵌套串联全 Pipeline |
| AWS 基础 | X-Ray `TracingConfig: Active` | Lambda 冷启动/SDK 调用（零代码） |

Langfuse 配置（可选）：在 `.env` 中设置 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`，`make setup` 自动注入到 Lambda 环境变量。Scheduler `@observe(name="photo_pipeline")` 作为 root span；trace_id（`create_trace_id(seed=request_id)` 确定性生成）和自己的 `parent_observation_id` 随 SFN input 传下去；下游 Lambda 通过 `@observe` 的 `langfuse_trace_id` + `langfuse_parent_observation_id` kwarg 真嵌套在 root 下，UI 里是一棵树而非平铺。

## 项目结构

```
packages/common/          共享代码（DB、BatchManager、Config、Tracing）
services/
├── scheduler/            Lambda：创建 batch，启动状态机（parent trace）
├── worker/               ECS/Batch：InsightFace 人脸检测 + 512 维 embedding
├── get_photo_ids/        Lambda：查询 photo_ids 供 Map 使用
├── tagger/               Lambda：照片打标（Stage 2，mock，@observe）
├── vlm_extractor/        Lambda：VLM 结构化提取（Stage 3，mock，@observe）
└── mark_complete/        Lambda：标记 batch 完成
terraform/                IaC 配置（local.tfvars / aws.tfvars 切换环境）
state-machines/           Step Functions 状态机定义（local + AWS）
migrations/               dbmate SQL 迁移
docker/models/            InsightFace 模型（.gitignore 不入库，make download-models 预下载）
docs/superpowers/specs/   设计文档归档
```

## MiniStack 已知限制（本地 e2e 相关）

- **`ecs:runTask.sync` 会 hang**：task 注册为 RUNNING 但不真跑，也不自动 STOPPED。e2e 脚本在 `start_execution` 后主动 `ecs.stop_task` 解锁（见 `scripts/test_e2e.py:_unlock_ministack_ecs`），真实数据靠预跑 `docker compose run worker` 填入
- **SFN Activity runtime 不调度**：create_activity/send_task_* CRUD 可用，但 state machine 里挂 Activity Resource 时，`get_activity_task` 恒返回空 token → 本地不用 Activity 模式
- 详细背景见 `docs/superpowers/specs/2026-04-20-sfn-activity-e2e-design.md`

## 技术栈

Python 3.12 · uv workspace · OpenTofu · MiniStack · pgvector · InsightFace · Step Functions · Langfuse

## CI

GitHub Actions：push/PR → Ruff lint + format → dbmate migrate → pytest（pgvector service container）

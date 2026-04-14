# 多阶段照片处理 Pipeline：Step Functions + 可观测性设计

**日期**: 2026-04-14
**范围**: Step Functions 编排多阶段 Pipeline（Worker → Tagger → VLM → Complete），LangSmith + X-Ray 可观测性，mock LLM Lambda
**不包含**: 真实 LLM 接入、AWS 部署、Supabase 迁移

---

## 1. 背景与目标

当前 Pipeline 只有 Stage 1（Worker 人脸检测），且各环节通过 e2e 脚本手动串联。本次目标：

1. **Step Functions 编排**：用状态机串联 Worker → Tagger → VLM → MarkComplete，替代手动脚本串联
2. **Stage 2/3 mock Lambda**：mock 打标和 VLM 提取，验证完整多阶段链路
3. **并行处理**：Stage 2/3 用 Map state 逐张照片并行处理
4. **可观测性**：LangSmith `@traceable` + X-Ray Lambda 原生集成
5. **本地/AWS 对齐**：状态机定义仅 RunWorker 一处差异（ECS vs Batch）

### 设计原则

- **MiniStack 原生支持**：Step Functions + ECS RunTask.sync + Lambda invoke 全部在 MiniStack 可用
- **零 SQS**：不再需要 SQS 队列和 DLQ，Step Functions 内置 Retry/Catch
- **mock 先行**：Stage 2/3 返回假数据，未来只改函数内部逻辑，Pipeline 结构不变

---

## 2. 整体架构

```
Scheduler Lambda
    │
    ▼ sfn.start_execution()
Step Functions 状态机
    │
    ├── RunWorker (batch:submitJob.sync / ecs:runTask.sync)
    │     所有照片一起做人脸检测 + embedding 写入
    │     .sync 同步等全部完成
    │
    ├── GetPhotoIds (lambda:invoke)
    │     从 DB 查 batch 下所有 photo_id，供 Map 拆分
    │
    ├── TagPhotos (Map, MaxConcurrency=10)
    │     ├── photo_1 → Tagger Lambda (mock)
    │     ├── photo_2 → Tagger Lambda
    │     └── photo_N → Tagger Lambda
    │     全部完成后汇合
    │
    ├── VLMExtract (Map, MaxConcurrency=10)
    │     ├── photo_1 → VLM Lambda (mock)
    │     ├── photo_2 → VLM Lambda
    │     └── photo_N → VLM Lambda
    │     全部完成后汇合
    │
    └── MarkComplete (lambda:invoke)
          标记 batch_status = 'completed'
```

### 本地 vs AWS

| 组件 | 本地 (MiniStack) | AWS | 差异 |
|------|-----------------|-----|------|
| 状态机引擎 | MiniStack Step Functions | AWS Step Functions | 零 |
| RunWorker | `ecs:runTask.sync`（真 Docker, CPU） | `batch:submitJob.sync`（GPU, g4dn） | Resource ARN 不同 |
| GetPhotoIds | Lambda Container Image | Lambda Container Image | 零 |
| Tagger | Lambda Container Image | Lambda Container Image | 零 |
| VLM Extractor | Lambda Container Image | Lambda Container Image | 零 |
| MarkComplete | Lambda Container Image | Lambda Container Image | 零 |

**差异封装**：两份状态机定义 `pipeline-local.json` 和 `pipeline-aws.json`，仅 RunWorker state 不同。

---

## 3. 新增服务

### 3.1 项目结构

```
services/
├── scheduler/          # 已有 → 改为启动状态机
├── worker/             # 已有 → 不变
├── get_photo_ids/      # 新增：查 photo_ids 喂给 Map
├── tagger/             # 新增：Stage 2 mock 打标
├── vlm_extractor/      # 新增：Stage 3 mock VLM
├── mark_complete/      # 新增：标记 batch 完成
└── timeout_checker/    # 已有 placeholder

state-machines/
├── pipeline-local.json
└── pipeline-aws.json
```

### 3.2 Scheduler 改动

```python
# handler.py — 改动部分
def handler(event, context):
    # ...create_batch 不变...

    # 替换 submit_job / is_local 分支 → 统一启动状态机
    sfn = boto3.client("stepfunctions")
    sfn.start_execution(
        stateMachineArn=os.environ["STATE_MACHINE_ARN"],
        name=f"batch-{batch_id}",
        input=json.dumps({"batch_id": batch_id, "s3_keys": s3_keys}),
    )

    return {"statusCode": 200, "body": json.dumps({"batch_id": batch_id})}
```

Scheduler 不再有 `is_local()` 分支——本地和 AWS 都调 `start_execution`，差异在状态机定义里。

### 3.3 GetPhotoIds Lambda（新增）

```python
def handler(event, context):
    batch_id = event["batch_id"]
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT photo_id FROM photos WHERE batch_id = %s", (batch_id,))
        photo_ids = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
    return {"batch_id": batch_id, "photo_ids": photo_ids}
```

**为什么需要**：ECS RunTask.sync 返回 task metadata 而非容器 stdout。用轻量 Lambda 从 DB 查 photo_ids 喂给后续 Map state。

### 3.4 Tagger Lambda（新增，mock）

```python
from langsmith import traceable

@traceable(name="stage2_tag_photo")
def handler(event, context):
    photo_id = event["photo_id"]
    tags = {"scene": "outdoor", "objects": ["person", "tree"], "mood": "happy"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE photos SET tags = %s WHERE photo_id = %s",
                    (json.dumps(tags), photo_id))
        conn.commit()
    finally:
        conn.close()
    return {"photo_id": photo_id, "status": "tagged"}
```

**未来替换**：`tags = mock_data` → `tags = claude_client.messages.create(...)` 调用 Claude Vision API。

### 3.5 VLM Extractor Lambda（新增，mock）

```python
from langsmith import traceable

@traceable(name="stage3_vlm_extract")
def handler(event, context):
    photo_id = event["photo_id"]
    vlm_result = {
        "description": "A person standing in a park",
        "entities": [{"type": "person", "name": "unknown"}],
        "location_guess": "urban park",
    }

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE photos SET vlm_result = %s WHERE photo_id = %s",
                    (json.dumps(vlm_result), photo_id))
        conn.commit()
    finally:
        conn.close()
    return {"photo_id": photo_id, "status": "extracted"}
```

### 3.6 MarkComplete Lambda（新增）

```python
def handler(event, context):
    batch_id = event["batch_id"]
    conn = get_connection()
    try:
        mgr = PgBatchManager(conn)
        mgr.mark_batch_complete(batch_id)
        conn.commit()
    finally:
        conn.close()
    return {"batch_id": batch_id, "status": "completed"}
```

### 3.7 Dockerfile 模板

所有新增 Lambda 共用与 scheduler 相同的 Dockerfile 模板：

```dockerfile
FROM public.ecr.aws/lambda/python:3.12 AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/<service>/ services/<service>/
RUN uv sync --package <service> --no-install-workspace --no-dev --frozen
RUN uv sync --package <service> --no-dev --frozen

FROM public.ecr.aws/lambda/python:3.12
COPY --from=builder /app/.venv/lib/python3.12/site-packages/ ${LAMBDA_TASK_ROOT}/
COPY services/<service>/src/<service>/ ${LAMBDA_TASK_ROOT}/<service>/
COPY packages/common/src/common/ ${LAMBDA_TASK_ROOT}/common/
CMD ["<service>.handler.handler"]
```

---

## 4. 数据库变更

### Migration 002

```sql
-- migrate:up
ALTER TABLE photos ADD COLUMN IF NOT EXISTS tags JSONB;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS vlm_result JSONB;

-- migrate:down
ALTER TABLE photos DROP COLUMN IF EXISTS vlm_result;
ALTER TABLE photos DROP COLUMN IF EXISTS tags;
```

### Schema 总览（变更后）

```
photos
├── photo_id     SERIAL PK
├── batch_id     INTEGER FK → photo_batches
├── s3_key       TEXT
├── status       TEXT (pending/completed/failed)
├── face_count   INTEGER          ← Stage 1 写入
├── tags         JSONB            ← Stage 2 写入（新增）
├── vlm_result   JSONB            ← Stage 3 写入（新增）
└── created_at   TIMESTAMPTZ
```

---

## 5. 状态机定义

### 本地版 (pipeline-local.json)

RunWorker 使用 `ecs:runTask.sync`：

```json
{
  "Comment": "Photo Pipeline: Worker → Tag (parallel) → VLM (parallel) → Complete",
  "StartAt": "RunWorker",
  "States": {
    "RunWorker": {
      "Type": "Task",
      "Resource": "arn:aws:states:::ecs:runTask.sync",
      "Parameters": {
        "Cluster": "${ECS_CLUSTER}",
        "TaskDefinition": "${WORKER_TASK_DEF}",
        "Overrides": {
          "ContainerOverrides": [{
            "Name": "worker",
            "Environment": [
              {"Name": "BATCH_ID", "Value.$": "States.Format('{}', $.batch_id)"},
              {"Name": "S3_KEYS", "Value.$": "States.JsonToString($.s3_keys)"},
              {"Name": "DATABASE_URL", "Value": "${DATABASE_URL}"},
              {"Name": "AWS_ENDPOINT_URL", "Value": "${AWS_ENDPOINT_URL}"}
            ]
          }]
        }
      },
      "ResultPath": "$.worker_output",
      "Retry": [{"ErrorEquals": ["States.ALL"], "MaxAttempts": 2, "BackoffRate": 2}],
      "Next": "GetPhotoIds"
    },
    "GetPhotoIds": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "${GET_PHOTO_IDS_FUNCTION}",
        "Payload": {"batch_id.$": "$.batch_id"}
      },
      "ResultSelector": {
        "batch_id.$": "$.Payload.batch_id",
        "photo_ids.$": "$.Payload.photo_ids"
      },
      "ResultPath": "$",
      "Next": "TagPhotos"
    },
    "TagPhotos": {
      "Type": "Map",
      "ItemsPath": "$.photo_ids",
      "MaxConcurrency": 10,
      "ItemProcessor": {
        "ProcessorConfig": {"Mode": "INLINE"},
        "StartAt": "TagOnePhoto",
        "States": {
          "TagOnePhoto": {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {
              "FunctionName": "${TAGGER_FUNCTION}",
              "Payload": {"photo_id.$": "$"}
            },
            "ResultSelector": {"photo_id.$": "$.Payload.photo_id"},
            "End": true
          }
        }
      },
      "ResultPath": "$.tag_results",
      "Next": "VLMExtract"
    },
    "VLMExtract": {
      "Type": "Map",
      "ItemsPath": "$.photo_ids",
      "MaxConcurrency": 10,
      "ItemProcessor": {
        "ProcessorConfig": {"Mode": "INLINE"},
        "StartAt": "ExtractOnePhoto",
        "States": {
          "ExtractOnePhoto": {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {
              "FunctionName": "${VLM_FUNCTION}",
              "Payload": {"photo_id.$": "$"}
            },
            "ResultSelector": {"photo_id.$": "$.Payload.photo_id"},
            "End": true
          }
        }
      },
      "ResultPath": "$.vlm_results",
      "Next": "MarkComplete"
    },
    "MarkComplete": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "${MARK_COMPLETE_FUNCTION}",
        "Payload": {"batch_id.$": "$.batch_id"}
      },
      "End": true
    }
  }
}
```

### AWS 版差异

仅 RunWorker state 替换为 Batch：

```json
"RunWorker": {
  "Type": "Task",
  "Resource": "arn:aws:states:::batch:submitJob.sync",
  "Parameters": {
    "JobName.$": "States.Format('face-detect-{}', $.batch_id)",
    "JobQueue": "${GPU_JOB_QUEUE}",
    "JobDefinition": "${JOB_DEFINITION}",
    "ContainerOverrides": {
      "Environment": [
        {"Name": "BATCH_ID", "Value.$": "States.Format('{}', $.batch_id)"},
        {"Name": "S3_KEYS", "Value.$": "States.JsonToString($.s3_keys)"}
      ]
    }
  },
  "ResultPath": "$.worker_output",
  "Retry": [{"ErrorEquals": ["States.ALL"], "MaxAttempts": 2, "BackoffRate": 2}],
  "Next": "GetPhotoIds"
}
```

---

## 6. 可观测性

### 三层可观测

| 层 | 工具 | 覆盖 | 成本 |
|----|------|------|------|
| **Pipeline 全局** | Step Functions 执行历史 | 每步耗时/输入/输出/错误/重试 | 免费（4000 次/月） |
| **LLM 节点** | LangSmith `@traceable` | Stage 2/3 的 mock（未来 LLM）调用细节 | 免费（5000 trace/月） |
| **AWS 基础** | X-Ray | Lambda 冷启动/执行时间/SDK 调用 | 免费（10 万 trace/月） |

### LangSmith 接入

Stage 2/3 handler 用 `@traceable` 装饰。环境变量：

```
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=<key>
LANGCHAIN_PROJECT=photo-pipeline
```

Step Functions 执行 ID 作为 trace 的自然关联——同一次执行中的所有 Lambda 在 LangSmith 中通过 `run_name` 标记 batch_id 关联。

### X-Ray 接入

每个 Lambda 配置 `TracingConfig: Active`，零代码。本地 MiniStack 不支持 X-Ray，自动忽略。

---

## 7. setup_ministack.py 变更

需要新增：

```python
# 1. 创建 ECS 集群 + 注册 Task Definition（Worker 容器）
ecs = boto3.client("ecs")
ecs.create_cluster(clusterName="photo-pipeline")
ecs.register_task_definition(
    family="photo-worker",
    containerDefinitions=[{
        "name": "worker",
        "image": "photo-worker:latest",
        "essential": True,
    }],
)

# 2. 部署新增 Lambda（get_photo_ids, tagger, vlm_extractor, mark_complete）
for fn_name, image in [
    ("get-photo-ids", "get-photo-ids:latest"),
    ("photo-tagger", "photo-tagger:latest"),
    ("photo-vlm", "photo-vlm:latest"),
    ("photo-mark-complete", "photo-mark-complete:latest"),
]:
    lam.create_function(FunctionName=fn_name, PackageType="Image", ...)

# 3. 创建 Step Functions 状态机
sfn = boto3.client("stepfunctions")
sfn.create_state_machine(
    name="photo-pipeline",
    definition=open("state-machines/pipeline-local.json").read(),
    roleArn="arn:aws:iam::000000000000:role/sfn-role",
)
```

---

## 8. e2e 测试变更

```python
def main():
    # 1. 上传照片到 S3（不变）
    # 2. 调用 Scheduler handler（启动状态机）
    # 3. 等待状态机执行完成
    sfn = boto3.client("stepfunctions")
    # poll execution status
    while True:
        resp = sfn.describe_execution(executionArn=execution_arn)
        if resp["status"] in ("SUCCEEDED", "FAILED", "TIMED_OUT"):
            break
        time.sleep(2)
    assert resp["status"] == "SUCCEEDED"

    # 4. 验证 DB
    #    - batch status = completed
    #    - photos: face_count > 0, tags IS NOT NULL, vlm_result IS NOT NULL
    #    - face_embeddings: 512 维向量存在
```

---

## 9. 依赖变更

### pyproject.toml (root)

```toml
[tool.uv.workspace]
members = ["packages/*", "services/*"]  # 已包含新增 services

[dependency-groups]
dev = [
    "numpy>=2.4.4",
    "pytest>=8.0.0",
    "ruff>=0.11.0",
    "langsmith>=0.3.0",  # 新增
]
```

### 新增 services 的 pyproject.toml

tagger / vlm_extractor / mark_complete / get_photo_ids 各自的依赖：

```toml
[project]
dependencies = ["common", "boto3>=1.28.0"]

[tool.uv.sources]
common = { workspace = true }
```

tagger 和 vlm_extractor 额外依赖 `langsmith`。

---

## 10. 不包含（后续阶段）

- 真实 LLM 接入（Claude Vision API 替换 mock）
- AWS 部署（ECR 推送、Batch 配置、Supabase 迁移）
- 人脸聚类（DBSCAN on pgvector embeddings）
- Timeout checker 实现（Step Functions 内置超时可部分替代）
- 前端 GraphQL API

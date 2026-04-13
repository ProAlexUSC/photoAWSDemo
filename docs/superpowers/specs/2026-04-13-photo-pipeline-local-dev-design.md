# 照片处理 Pipeline：本地开发环境设计

**日期**: 2026-04-13
**范围**: 本地开发环境搭建，能跑通 MiniStack + pgvector + Worker CPU 模式的 e2e 流程
**不包含**: AWS 部署、多 Job 并行、Redis DECR、Step Functions、VLM 多阶段 Pipeline

---

## 1. 项目概述

独立的照片处理 Pipeline 项目。核心功能：用户上传照片 → Lambda Scheduler 写 DB 并提交 Batch Job → GPU Worker 做 InsightFace 人脸检测 → 512 维 embedding 写入 pgvector。

本次目标：搭建本地开发环境，用 MiniStack 模拟 AWS 服务（S3、Lambda、SQS），pgvector Docker 做数据库，Worker 以 CPU 模式本地运行，e2e 测试验证全链路。

## 2. 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 语言 | Python | InsightFace/onnxruntime 生态，笔记已有设计 |
| 依赖管理 | uv workspace | 每个 service 独立依赖树，共享代码 editable install |
| 本地 AWS 模拟 | MiniStack (Docker) | MIT 免费，35+ AWS 服务，LocalStack 兼容 API |
| 数据库 | pgvector/pgvector:pg16 (Docker) | 内置 pgvector 扩展，Supabase/RDS 兼容 |
| 数据库迁移 | dbmate | 语言无关，纯 SQL，用户已研究过 |
| 人脸检测 | InsightFace buffalo_l | 开源，GPU 快（10-30ms/张），CPU 兼容（~200ms/张） |
| embedding | onnxruntime (CPU/CUDA 自动切换) | 本地 CPU 开发，AWS GPU 部署，结果一致 |

## 3. 项目结构

```
photoAWS/
├── pyproject.toml                    # uv workspace 根
├── uv.lock                          # 全局统一锁文件
├── docker-compose.yml                # MiniStack + pgvector
├── Makefile                          # up/down/setup/test/test-e2e/migrate
├── .env.example
│
├── packages/
│   └── common/                       # 共享代码
│       ├── pyproject.toml
│       └── src/common/
│           ├── __init__.py
│           ├── config.py             # LOCAL_DEV 切换 boto3 endpoint
│           ├── db.py                 # psycopg2 连接管理
│           ├── batch_manager.py      # PgBatchManager（幂等写入、原子完成检查）
│           └── models.py             # BatchStatus enum, dataclass
│
├── services/
│   ├── scheduler/                    # Lambda: 接收请求 → 写 DB → 提交 Batch Job
│   │   ├── pyproject.toml            # depends on common (workspace)
│   │   └── src/scheduler/
│   │       ├── __init__.py
│   │       └── handler.py
│   ├── worker/                       # AWS Batch GPU / 本地 CPU
│   │   ├── pyproject.toml            # depends on common + insightface + onnxruntime
│   │   ├── Dockerfile                # nvidia/cuda 基础镜像（部署用）
│   │   └── src/worker/
│   │       ├── __init__.py
│   │       └── main.py               # 人脸检测 + embedding 写入
│   └── timeout_checker/              # Lambda: EventBridge 定时扫描超时 batch
│       ├── pyproject.toml            # depends on common (workspace)
│       └── src/timeout_checker/
│           ├── __init__.py
│           └── handler.py            # 空占位，本次不实现逻辑
│
├── migrations/                       # dbmate SQL 迁移
│   ├── 001_create_schema.up.sql
│   └── 001_create_schema.down.sql
│
├── scripts/
│   ├── setup_ministack.py            # 初始化 S3 bucket + SQS DLQ + 部署 Lambda
│   └── test_e2e.py                   # 端到端测试
│
└── tests/                            # 单元测试
    ├── fixtures/                     # 2-3 张含人脸测试图片 (< 100KB)
    ├── test_config.py
    ├── test_scheduler.py
    └── test_worker.py
```

### uv workspace 配置

根 `pyproject.toml`:

```toml
[project]
name = "photo-pipeline"
version = "0.1.0"
requires-python = ">=3.12"

[tool.uv.workspace]
members = ["packages/*", "services/*"]
```

各 service 引用 common:

```toml
# services/scheduler/pyproject.toml
[project]
name = "scheduler"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["common", "boto3"]

[tool.uv.sources]
common = { workspace = true }
```

Worker 额外依赖:

```toml
# services/worker/pyproject.toml
[project]
name = "worker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "common",
    "boto3",
    "insightface",
    "onnxruntime",
    "opencv-python-headless",
    "numpy",
]

[tool.uv.sources]
common = { workspace = true }
```

## 4. Docker Compose

```yaml
services:
  ministack:
    image: nahuelnucera/ministack
    ports:
      - "4566:4566"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock

  postgres:
    image: pgvector/pgvector:pg16
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB: photo_pipeline
      POSTGRES_USER: dev
      POSTGRES_PASSWORD: dev
```

不使用 `docker-entrypoint-initdb.d` 自动建表，由 dbmate 管理迁移。

### dbmate 配置

dbmate 通过 `DATABASE_URL` 环境变量连接数据库。在 `.env` 中设置后，直接运行 `dbmate up` 即可。迁移文件放在 `migrations/` 目录（dbmate 默认路径）。

```bash
# .env 中已有 DATABASE_URL，dbmate 自动读取
dbmate up       # 运行所有 pending 迁移
dbmate down     # 回滚最近一次迁移
dbmate status   # 查看迁移状态
```

## 5. 数据库 Schema

单个迁移文件 `001_create_schema.up.sql`：

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE photo_batches (
    batch_id    SERIAL PRIMARY KEY,
    request_id  TEXT UNIQUE,                        -- 幂等键
    user_id     INTEGER NOT NULL,
    total       INTEGER NOT NULL,
    completed   INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending',    -- pending/processing/completed/timeout
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE photos (
    photo_id    SERIAL PRIMARY KEY,
    batch_id    INTEGER NOT NULL REFERENCES photo_batches(batch_id),
    s3_key      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',    -- pending/processing/completed/failed
    face_count  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE face_embeddings (
    face_id     SERIAL PRIMARY KEY,
    photo_id    INTEGER NOT NULL REFERENCES photos(photo_id),
    embedding   vector(512) NOT NULL,
    bbox        JSONB,                              -- {x, y, w, h}
    cluster_id  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_face_embedding_hnsw
    ON face_embeddings USING hnsw (embedding vector_cosine_ops);
```

对应 `001_create_schema.down.sql`:

```sql
DROP TABLE IF EXISTS face_embeddings;
DROP TABLE IF EXISTS photos;
DROP TABLE IF EXISTS photo_batches;
DROP EXTENSION IF EXISTS vector;
```

## 6. 核心组件设计

### 6.1 common/config.py — 环境适配层

`LOCAL_DEV` 环境变量控制 boto3 指向 MiniStack 还是真实 AWS。所有模块统一调用 `aws_client(service)` 和 `get_database_url()`，业务代码零感知环境差异。

- `LOCAL_DEV=true` → `endpoint_url=http://localhost:4566`，测试凭证
- `LOCAL_DEV=false` → 标准 boto3 凭证链（IAM Role / 环境变量）

### 6.2 common/batch_manager.py — 数据访问层

`PgBatchManager` 封装所有数据库操作：

- `create_batch(request_id, user_id, s3_keys)` → 幂等创建 batch + photos 记录，`ON CONFLICT DO NOTHING`
- `get_photo_id(batch_id, s3_key)` → 查询 photo_id
- `insert_embedding(photo_id, embedding, bbox)` → 插入 face_embeddings
- `mark_photo_complete(photo_id, face_count)` → 更新 photo status + face_count
- `mark_batch_complete(batch_id)` → 检查所有 photos 完成后更新 batch status

### 6.3 services/scheduler/handler.py — Lambda 调度器

接收参数 `{request_id, user_id, s3_keys}`：

1. 调用 `batch_manager.create_batch()` 写 DB
2. LOCAL 模式：不调 Batch API（MiniStack 不支持），直接返回 `batch_id`
3. AWS 模式：调 `batch.submit_job()` 提交 GPU Job

**MiniStack Lambda 注意事项**：Lambda 跑在 Docker 容器内，连接宿主机 PostgreSQL 需要用 `host.docker.internal:5432`，在 `setup_ministack.py` 部署时通过环境变量 `DATABASE_URL` 配置。

### 6.4 services/worker/main.py — 人脸检测 Worker

启动时加载 InsightFace buffalo_l 模型，自动检测 CUDA/CPU：

1. 从环境变量读取 `BATCH_ID` + `S3_KEYS`
2. 逐张：S3 下载 → `model.get(img)` 检测人脸 → 写 embedding 到 DB
3. 每张完成后 `mark_photo_complete()`
4. 全部完成后 `mark_batch_complete()`

本地运行：`uv run --package worker python -m worker.main`（CPU 模式，~200ms/张）。

### 6.5 services/timeout_checker/handler.py

本次只建空占位文件，不实现逻辑。AWS 部署时由 EventBridge 定时触发，扫描 `status='processing' AND created_at < now() - 1 hour` 的 batch 标记为 timeout。

## 7. 本地开发流程

### setup_ministack.py

启动后一次性运行，初始化 MiniStack 资源：

1. 创建 S3 bucket `photo-uploads`
2. 创建 SQS DLQ `scheduler-dlq`
3. 打包 scheduler 代码为 zip（从 `services/scheduler/src/` 和 `packages/common/src/` 收集）
4. 部署 Lambda 到 MiniStack，配置 `DATABASE_URL=postgresql://dev:dev@host.docker.internal:5432/photo_pipeline`

### test_e2e.py

全链路验证：

1. 上传 3 张测试照片到 MiniStack S3
2. 调用 MiniStack Lambda (`photo-scheduler`)
3. 拿到 `batch_id`
4. 本地运行 Worker（CPU 模式），传入 `BATCH_ID` 和 `S3_KEYS` 环境变量
5. 查 DB 验证：
   - `photo_batches.status == 'completed'`
   - 所有 photos status == 'completed'
   - `face_embeddings` 有记录且 embedding 维度 == 512

### Makefile

```makefile
.PHONY: up down migrate setup test test-e2e

up:
	docker compose up -d
	@sleep 3

down:
	docker compose down

migrate:
	dbmate up

setup: up migrate
	uv run python scripts/setup_ministack.py

test:
	uv run pytest tests/ -v

test-e2e: setup
	uv run python scripts/test_e2e.py
```

## 8. 环境变量

### .env.example

```bash
LOCAL_DEV=true
DATABASE_URL=postgresql://dev:dev@localhost:5432/photo_pipeline
```

### 本地 vs AWS 对照

| 变量 | 本地 | AWS Lambda | AWS Batch Worker |
|------|------|-----------|-----------------|
| `LOCAL_DEV` | `true` | `false` | `false` |
| `DATABASE_URL` | `pg://dev:dev@localhost:5432/photo_pipeline` | `pg://...@xxx.supabase.co:6543/postgres` | `pg://...@xxx.supabase.co:5432/postgres` |
| `GPU_JOB_QUEUE` | — | `arn:aws:batch:...` | — |
| `JOB_DEFINITION` | — | `arn:aws:batch:...` | — |
| `BATCH_ID` | e2e 脚本传入 | — | Batch 容器注入 |
| `S3_KEYS` | e2e 脚本传入 | — | Batch 容器注入 |

## 9. 测试策略

### 单元测试

- `test_config.py` — 验证 LOCAL_DEV 切换逻辑，mock boto3
- `test_scheduler.py` — 验证 handler 解析参数、调用 batch_manager，mock DB
- `test_worker.py` — 验证图片处理流程，mock InsightFace model + S3

### E2E 测试

- `test_e2e.py` — 真实 MiniStack + pgvector，完整链路，需要 `docker compose up`

### 测试 fixtures

`tests/fixtures/` 下 2-3 张含人脸的小图（< 100KB），用于 e2e 和单元测试。

## 10. 不做的事

- AWS 部署脚本（push-worker, deploy-lambda）— 留到部署阶段
- 多 Job 并行拆分 — Stage 1 单 Job 即可
- timeout_checker 逻辑实现 — 空占位
- Redis 计数优化 — 纯 PG 方案
- VLM 多阶段 Pipeline — 只做人脸检测
- S3 event trigger 自动触发 Lambda — 手动/脚本调用
- CI/CD — 留到部署阶段

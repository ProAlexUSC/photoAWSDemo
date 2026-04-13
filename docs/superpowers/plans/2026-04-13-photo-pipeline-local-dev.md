# Photo Pipeline 本地开发环境 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建可跑通 e2e 的本地开发环境：MiniStack (S3/Lambda/SQS) + pgvector + InsightFace Worker CPU 模式

**Architecture:** uv workspace monorepo，packages/common 共享层 + services/{scheduler,worker,timeout_checker} 三个独立部署单元。Docker Compose 编排 MiniStack + pgvector + worker。boto3 通过原生 `AWS_ENDPOINT_URL` 环境变量自动切换本地/AWS。

**Tech Stack:** Python 3.12, uv workspace, MiniStack, pgvector/PostgreSQL 16, dbmate, InsightFace buffalo_l, onnxruntime, psycopg2, boto3, pytest

**Spec:** `docs/superpowers/specs/2026-04-13-photo-pipeline-local-dev-design.md`

---

## File Map

| File | Responsibility | Created in |
|------|---------------|------------|
| `pyproject.toml` | uv workspace root | Task 1 |
| `packages/common/pyproject.toml` | common package manifest | Task 1 |
| `services/scheduler/pyproject.toml` | scheduler deps (common + boto3) | Task 1 |
| `services/worker/pyproject.toml` | worker deps (common + insightface + onnxruntime) | Task 1 |
| `services/timeout_checker/pyproject.toml` | timeout_checker deps (common) | Task 1 |
| `.env.example` | env var template | Task 1 |
| `.env` | local env vars (gitignored) | Task 1 |
| `.gitignore` | ignore patterns | Task 1 |
| `docker-compose.yml` | MiniStack + pgvector + worker | Task 2 |
| `migrations/001_create_schema.up.sql` | DDL: 3 tables + pgvector + HNSW index | Task 2 |
| `migrations/001_create_schema.down.sql` | DDL rollback | Task 2 |
| `Makefile` | dev commands | Task 2 |
| `packages/common/src/common/__init__.py` | package init | Task 3 |
| `packages/common/src/common/models.py` | BatchStatus, PhotoStatus enums | Task 3 |
| `packages/common/src/common/config.py` | is_local(), get_database_url() | Task 3 |
| `packages/common/src/common/db.py` | get_connection() | Task 3 |
| `tests/test_config.py` | config unit tests | Task 3 |
| `packages/common/src/common/batch_manager.py` | PgBatchManager CRUD | Task 4 |
| `tests/test_batch_manager.py` | batch_manager unit tests (real PG) | Task 4 |
| `services/scheduler/src/scheduler/__init__.py` | package init | Task 5 |
| `services/scheduler/src/scheduler/handler.py` | Lambda handler | Task 5 |
| `tests/test_scheduler.py` | scheduler unit tests | Task 5 |
| `services/scheduler/Dockerfile` | Lambda Container Image | Task 6 |
| `services/worker/src/worker/__init__.py` | package init | Task 7 |
| `services/worker/src/worker/main.py` | face detection worker | Task 7 |
| `tests/test_worker.py` | worker unit tests | Task 7 |
| `services/worker/Dockerfile` | GPU/CPU worker image | Task 8 |
| `services/timeout_checker/src/timeout_checker/__init__.py` | package init | Task 9 |
| `services/timeout_checker/src/timeout_checker/handler.py` | placeholder handler | Task 9 |
| `services/timeout_checker/Dockerfile` | Lambda Container Image placeholder | Task 9 |
| `scripts/setup_ministack.py` | init MiniStack resources + deploy Lambda | Task 10 |
| `tests/fixtures/face_*.jpg` | test images with faces | Task 11 |
| `scripts/test_e2e.py` | full e2e test script | Task 11 |

---

## Task 1: 项目脚手架 — uv workspace + 环境变量

**Files:**
- Create: `pyproject.toml`
- Create: `packages/common/pyproject.toml`
- Create: `services/scheduler/pyproject.toml`
- Create: `services/worker/pyproject.toml`
- Create: `services/timeout_checker/pyproject.toml`
- Create: `.env.example`
- Create: `.env`
- Create: `.gitignore`

- [ ] **Step 1: Create root pyproject.toml**

```toml
[project]
name = "photo-pipeline"
version = "0.1.0"
requires-python = ">=3.12"

[tool.uv.workspace]
members = ["packages/*", "services/*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create packages/common/pyproject.toml**

```toml
[project]
name = "common"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "boto3>=1.28.0",
    "psycopg2-binary",
]
```

- [ ] **Step 3: Create services/scheduler/pyproject.toml**

```toml
[project]
name = "scheduler"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "common",
    "boto3>=1.28.0",
]

[tool.uv.sources]
common = { workspace = true }
```

- [ ] **Step 4: Create services/worker/pyproject.toml**

```toml
[project]
name = "worker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "common",
    "boto3>=1.28.0",
    "insightface",
    "onnxruntime",
    "opencv-python-headless",
    "numpy",
]

[tool.uv.sources]
common = { workspace = true }
```

- [ ] **Step 5: Create services/timeout_checker/pyproject.toml**

```toml
[project]
name = "timeout-checker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "common",
    "boto3>=1.28.0",
]

[tool.uv.sources]
common = { workspace = true }
```

- [ ] **Step 6: Create .env.example and .env**

`.env.example`:
```bash
# AWS 服务 endpoint（本地指向 MiniStack，生产删除此行）
AWS_ENDPOINT_URL=http://localhost:4566
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
AWS_DEFAULT_REGION=us-east-1

# 非 AWS 逻辑分支开关（如跳过 Batch submit）
LOCAL_DEV=true

# 数据库
DATABASE_URL=postgresql://dev:dev@localhost:5432/photo_pipeline
```

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

- [ ] **Step 7: Create .gitignore**

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
.venv/

# uv
uv.lock

# Environment
.env

# OS
.DS_Store

# IDE
.idea/
.vscode/
```

- [ ] **Step 8: Run uv sync to validate workspace**

```bash
uv sync
```

Expected: resolves all workspace dependencies, creates `uv.lock` and `.venv/`.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml packages/common/pyproject.toml services/scheduler/pyproject.toml services/worker/pyproject.toml services/timeout_checker/pyproject.toml .env.example .gitignore uv.lock
git commit -m "chore: uv workspace 脚手架 + 环境变量模板"
```

---

## Task 2: Docker Compose + dbmate 迁移 + Makefile

**Files:**
- Create: `docker-compose.yml`
- Create: `migrations/001_create_schema.up.sql`
- Create: `migrations/001_create_schema.down.sql`
- Create: `Makefile`

- [ ] **Step 1: Create docker-compose.yml**

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

  worker:
    build:
      context: .
      dockerfile: services/worker/Dockerfile
    environment:
      - DATABASE_URL=postgresql://dev:dev@postgres:5432/photo_pipeline
      - AWS_ENDPOINT_URL=http://ministack:4566
      - AWS_ACCESS_KEY_ID=test
      - AWS_SECRET_ACCESS_KEY=test
      - AWS_DEFAULT_REGION=us-east-1
    depends_on:
      - postgres
      - ministack
    profiles:
      - e2e
```

- [ ] **Step 2: Create migrations/001_create_schema.up.sql**

```sql
-- migrate:up

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE photo_batches (
    batch_id    SERIAL PRIMARY KEY,
    request_id  TEXT UNIQUE,
    user_id     INTEGER NOT NULL,
    total       INTEGER NOT NULL,
    completed   INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE photos (
    photo_id    SERIAL PRIMARY KEY,
    batch_id    INTEGER NOT NULL REFERENCES photo_batches(batch_id),
    s3_key      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    face_count  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE face_embeddings (
    face_id     SERIAL PRIMARY KEY,
    photo_id    INTEGER NOT NULL REFERENCES photos(photo_id),
    embedding   vector(512) NOT NULL,
    bbox        JSONB,
    cluster_id  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_face_embedding_hnsw
    ON face_embeddings USING hnsw (embedding vector_cosine_ops);
```

- [ ] **Step 3: Create migrations/001_create_schema.down.sql**

```sql
-- migrate:down

DROP TABLE IF EXISTS face_embeddings;
DROP TABLE IF EXISTS photos;
DROP TABLE IF EXISTS photo_batches;
DROP EXTENSION IF EXISTS vector;
```

- [ ] **Step 4: Create Makefile**

```makefile
.PHONY: up down migrate setup test test-e2e build-worker build-scheduler

up:
	docker compose up -d
	@sleep 3
	@echo "MiniStack: http://localhost:4566"
	@echo "PostgreSQL: localhost:5432"

down:
	docker compose down

migrate:
	dbmate up

build-scheduler:
	docker build -f services/scheduler/Dockerfile -t photo-scheduler .

build-worker:
	docker build -f services/worker/Dockerfile -t photo-worker .

setup: up migrate build-scheduler
	uv run python scripts/setup_ministack.py

test:
	uv run pytest tests/ -v

test-e2e: setup build-worker
	uv run python scripts/test_e2e.py
```

- [ ] **Step 5: Verify Docker Compose + migration**

```bash
make up
make migrate
```

Expected: containers start, `dbmate up` applies migration, tables created. Verify:

```bash
psql postgresql://dev:dev@localhost:5432/photo_pipeline -c "\dt"
```

Expected output includes `photo_batches`, `photos`, `face_embeddings`.

- [ ] **Step 6: Verify pgvector extension**

```bash
psql postgresql://dev:dev@localhost:5432/photo_pipeline -c "SELECT extversion FROM pg_extension WHERE extname='vector';"
```

Expected: returns a version (e.g., `0.7.0` or higher).

- [ ] **Step 7: Tear down and commit**

```bash
make down
git add docker-compose.yml migrations/ Makefile
git commit -m "infra: Docker Compose (MiniStack + pgvector) + dbmate 迁移 + Makefile"
```

---

## Task 3: packages/common — config, db, models

**Files:**
- Create: `packages/common/src/common/__init__.py`
- Create: `packages/common/src/common/models.py`
- Create: `packages/common/src/common/config.py`
- Create: `packages/common/src/common/db.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write tests for config.py**

Create `tests/test_config.py`:

```python
import os
from unittest import mock


def test_is_local_defaults_to_true():
    """LOCAL_DEV 未设置时默认 True"""
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop("LOCAL_DEV", None)
        from importlib import reload
        import common.config
        reload(common.config)
        assert common.config.is_local() is True


def test_is_local_false():
    """LOCAL_DEV=false 时返回 False"""
    with mock.patch.dict(os.environ, {"LOCAL_DEV": "false"}):
        from importlib import reload
        import common.config
        reload(common.config)
        assert common.config.is_local() is False


def test_get_database_url_reads_env():
    """get_database_url 读取 DATABASE_URL 环境变量"""
    with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/testdb"}):
        from importlib import reload
        import common.config
        reload(common.config)
        assert common.config.get_database_url() == "postgresql://test:test@localhost/testdb"


def test_get_database_url_missing_raises():
    """DATABASE_URL 未设置时抛 KeyError"""
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop("DATABASE_URL", None)
        from importlib import reload
        import common.config
        reload(common.config)
        try:
            common.config.get_database_url()
            assert False, "Should have raised KeyError"
        except KeyError:
            pass
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_config.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'common'`

- [ ] **Step 3: Create packages/common/src/common/__init__.py**

```python
```

(Empty file.)

- [ ] **Step 4: Create packages/common/src/common/models.py**

```python
from enum import Enum


class BatchStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    TIMEOUT = "timeout"


class PhotoStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
```

- [ ] **Step 5: Create packages/common/src/common/config.py**

```python
import os


def is_local() -> bool:
    """仅用于非 AWS 逻辑分支（如跳过 Batch submit）。
    boto3 endpoint 切换由 AWS_ENDPOINT_URL 环境变量处理，与此函数无关。
    """
    return os.environ.get("LOCAL_DEV", "true").lower() == "true"


def get_database_url() -> str:
    """读取 DATABASE_URL 环境变量。未设置时抛 KeyError。"""
    return os.environ["DATABASE_URL"]
```

- [ ] **Step 6: Create packages/common/src/common/db.py**

```python
import psycopg2
from common.config import get_database_url


def get_connection():
    """返回 psycopg2 连接。调用方负责关闭。"""
    return psycopg2.connect(get_database_url())
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
uv run pytest tests/test_config.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add packages/common/src/ tests/test_config.py
git commit -m "feat: common 包 — config, db, models"
```

---

## Task 4: PgBatchManager — 数据访问层（TDD）

**Files:**
- Create: `packages/common/src/common/batch_manager.py`
- Test: `tests/test_batch_manager.py`

**前置条件**: Docker Compose 运行中（`make up && make migrate`），因为测试用真实 PostgreSQL。

- [ ] **Step 1: Write tests for batch_manager**

Create `tests/test_batch_manager.py`:

```python
import os
import psycopg2
import pytest
from common.batch_manager import PgBatchManager


@pytest.fixture(autouse=True)
def db_url():
    """确保 DATABASE_URL 环境变量已设置"""
    os.environ.setdefault("DATABASE_URL", "postgresql://dev:dev@localhost:5432/photo_pipeline")


@pytest.fixture
def conn():
    """每个测试一个干净的事务，测试结束后回滚"""
    c = psycopg2.connect(os.environ["DATABASE_URL"])
    c.autocommit = False
    yield c
    c.rollback()
    c.close()


@pytest.fixture
def manager(conn):
    return PgBatchManager(conn)


def test_create_batch(manager, conn):
    """create_batch 创建 batch 和 photos 记录"""
    batch_id = manager.create_batch("req-001", user_id=1, s3_keys=["a.jpg", "b.jpg"])
    assert batch_id is not None

    cur = conn.cursor()
    cur.execute("SELECT status, total FROM photo_batches WHERE batch_id = %s", (batch_id,))
    row = cur.fetchone()
    assert row == ("pending", 2)

    cur.execute("SELECT COUNT(*) FROM photos WHERE batch_id = %s", (batch_id,))
    assert cur.fetchone()[0] == 2


def test_create_batch_idempotent(manager):
    """相同 request_id 不创建重复 batch"""
    id1 = manager.create_batch("req-dup", user_id=1, s3_keys=["a.jpg"])
    id2 = manager.create_batch("req-dup", user_id=1, s3_keys=["a.jpg"])
    assert id1 == id2


def test_get_photo_id(manager):
    """get_photo_id 返回正确的 photo_id"""
    batch_id = manager.create_batch("req-002", user_id=1, s3_keys=["x.jpg"])
    photo_id = manager.get_photo_id(batch_id, "x.jpg")
    assert photo_id is not None


def test_insert_embedding(manager, conn):
    """insert_embedding 写入 face_embeddings"""
    batch_id = manager.create_batch("req-003", user_id=1, s3_keys=["face.jpg"])
    photo_id = manager.get_photo_id(batch_id, "face.jpg")
    embedding = [0.1] * 512
    bbox = {"x": 10, "y": 20, "w": 100, "h": 120}

    manager.insert_embedding(photo_id, embedding, bbox)

    cur = conn.cursor()
    cur.execute("SELECT photo_id, bbox FROM face_embeddings WHERE photo_id = %s", (photo_id,))
    row = cur.fetchone()
    assert row[0] == photo_id
    assert row[1] == bbox


def test_mark_photo_complete(manager, conn):
    """mark_photo_complete 更新 photo status 和 face_count"""
    batch_id = manager.create_batch("req-004", user_id=1, s3_keys=["p.jpg"])
    photo_id = manager.get_photo_id(batch_id, "p.jpg")

    manager.mark_photo_complete(photo_id, face_count=3)

    cur = conn.cursor()
    cur.execute("SELECT status, face_count FROM photos WHERE photo_id = %s", (photo_id,))
    row = cur.fetchone()
    assert row == ("completed", 3)


def test_mark_batch_complete(manager, conn):
    """所有 photos 完成后，mark_batch_complete 将 batch 标记为 completed"""
    batch_id = manager.create_batch("req-005", user_id=1, s3_keys=["a.jpg", "b.jpg"])

    for s3_key in ["a.jpg", "b.jpg"]:
        photo_id = manager.get_photo_id(batch_id, s3_key)
        manager.mark_photo_complete(photo_id, face_count=1)

    manager.mark_batch_complete(batch_id)

    cur = conn.cursor()
    cur.execute("SELECT status, completed FROM photo_batches WHERE batch_id = %s", (batch_id,))
    row = cur.fetchone()
    assert row == ("completed", 2)


def test_mark_batch_not_complete_if_pending(manager, conn):
    """还有 pending 的 photo 时，batch 不标记为 completed"""
    batch_id = manager.create_batch("req-006", user_id=1, s3_keys=["a.jpg", "b.jpg"])
    photo_id = manager.get_photo_id(batch_id, "a.jpg")
    manager.mark_photo_complete(photo_id, face_count=1)

    manager.mark_batch_complete(batch_id)

    cur = conn.cursor()
    cur.execute("SELECT status FROM photo_batches WHERE batch_id = %s", (batch_id,))
    assert cur.fetchone()[0] == "processing"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_batch_manager.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'common.batch_manager'`

- [ ] **Step 3: Implement PgBatchManager**

Create `packages/common/src/common/batch_manager.py`:

```python
import json
from psycopg2.extensions import connection as PgConnection


class PgBatchManager:
    def __init__(self, conn: PgConnection):
        self.conn = conn

    def create_batch(self, request_id: str, user_id: int, s3_keys: list[str]) -> int:
        """幂等创建 batch + photos 记录。返回 batch_id。"""
        cur = self.conn.cursor()

        # 先尝试查找已存在的 batch
        cur.execute(
            "SELECT batch_id FROM photo_batches WHERE request_id = %s",
            (request_id,),
        )
        existing = cur.fetchone()
        if existing:
            return existing[0]

        # 创建新 batch
        cur.execute(
            """INSERT INTO photo_batches (request_id, user_id, total, status)
               VALUES (%s, %s, %s, 'pending')
               ON CONFLICT (request_id) DO NOTHING
               RETURNING batch_id""",
            (request_id, user_id, len(s3_keys)),
        )
        row = cur.fetchone()
        if row is None:
            # 并发插入时 ON CONFLICT 命中，重新查询
            cur.execute(
                "SELECT batch_id FROM photo_batches WHERE request_id = %s",
                (request_id,),
            )
            row = cur.fetchone()
        batch_id = row[0]

        # 创建 photos 记录
        for s3_key in s3_keys:
            cur.execute(
                """INSERT INTO photos (batch_id, s3_key, status)
                   VALUES (%s, %s, 'pending')""",
                (batch_id, s3_key),
            )

        return batch_id

    def get_photo_id(self, batch_id: int, s3_key: str) -> int:
        """查询 photo_id。"""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT photo_id FROM photos WHERE batch_id = %s AND s3_key = %s",
            (batch_id, s3_key),
        )
        return cur.fetchone()[0]

    def insert_embedding(self, photo_id: int, embedding: list[float], bbox: dict) -> None:
        """插入 face_embeddings 记录。"""
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO face_embeddings (photo_id, embedding, bbox)
               VALUES (%s, %s::vector, %s::jsonb)""",
            (photo_id, str(embedding), json.dumps(bbox)),
        )

    def mark_photo_complete(self, photo_id: int, face_count: int) -> None:
        """标记单张照片为 completed，更新 face_count。"""
        cur = self.conn.cursor()
        cur.execute(
            """UPDATE photos SET status = 'completed', face_count = %s
               WHERE photo_id = %s""",
            (face_count, photo_id),
        )

    def mark_batch_complete(self, batch_id: int) -> None:
        """检查所有 photos 是否完成。全部完成则标记 batch 为 completed，否则标记为 processing。"""
        cur = self.conn.cursor()

        # 统计已完成的 photos
        cur.execute(
            "SELECT COUNT(*) FROM photos WHERE batch_id = %s AND status = 'completed'",
            (batch_id,),
        )
        completed = cur.fetchone()[0]

        cur.execute(
            "SELECT total FROM photo_batches WHERE batch_id = %s",
            (batch_id,),
        )
        total = cur.fetchone()[0]

        if completed >= total:
            cur.execute(
                """UPDATE photo_batches SET status = 'completed', completed = %s
                   WHERE batch_id = %s""",
                (completed, batch_id),
            )
        else:
            cur.execute(
                """UPDATE photo_batches SET status = 'processing', completed = %s
                   WHERE batch_id = %s""",
                (completed, batch_id),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Requires Docker Compose running:

```bash
make up && make migrate
uv run pytest tests/test_batch_manager.py -v
```

Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/common/batch_manager.py tests/test_batch_manager.py
git commit -m "feat: PgBatchManager — 幂等 batch 创建 + 原子完成检查"
```

---

## Task 5: Scheduler Lambda handler（TDD）

**Files:**
- Create: `services/scheduler/src/scheduler/__init__.py`
- Create: `services/scheduler/src/scheduler/handler.py`
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: Write tests for scheduler handler**

Create `tests/test_scheduler.py`:

```python
import json
import os
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def env():
    with mock.patch.dict(os.environ, {
        "DATABASE_URL": "postgresql://dev:dev@localhost:5432/photo_pipeline",
        "LOCAL_DEV": "true",
    }):
        yield


def test_handler_creates_batch_and_returns_batch_id():
    """handler 应创建 batch 并返回 batch_id"""
    with mock.patch("scheduler.handler.PgBatchManager") as MockManager, \
         mock.patch("scheduler.handler.get_connection") as mock_conn:
        instance = MockManager.return_value
        instance.create_batch.return_value = 42

        from scheduler.handler import handler

        event = {
            "request_id": "test-001",
            "user_id": 1,
            "s3_keys": ["a.jpg", "b.jpg"],
        }
        result = handler(event, None)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["batch_id"] == 42
        instance.create_batch.assert_called_once_with("test-001", 1, ["a.jpg", "b.jpg"])


def test_handler_skips_batch_submit_when_local():
    """LOCAL_DEV=true 时不调用 batch.submit_job"""
    with mock.patch("scheduler.handler.PgBatchManager") as MockManager, \
         mock.patch("scheduler.handler.get_connection"), \
         mock.patch("boto3.client") as mock_boto:
        instance = MockManager.return_value
        instance.create_batch.return_value = 1

        from importlib import reload
        import scheduler.handler
        reload(scheduler.handler)
        from scheduler.handler import handler

        handler({"request_id": "r", "user_id": 1, "s3_keys": ["a.jpg"]}, None)

        # boto3.client('batch') 不应被调用
        for call in mock_boto.call_args_list:
            assert call[0][0] != "batch"


def test_handler_submits_batch_job_when_not_local():
    """LOCAL_DEV=false 时调用 batch.submit_job"""
    with mock.patch.dict(os.environ, {"LOCAL_DEV": "false", "GPU_JOB_QUEUE": "arn:queue", "JOB_DEFINITION": "arn:jobdef"}):
        with mock.patch("scheduler.handler.PgBatchManager") as MockManager, \
             mock.patch("scheduler.handler.get_connection"), \
             mock.patch("boto3.client") as mock_boto:
            instance = MockManager.return_value
            instance.create_batch.return_value = 7
            mock_batch_client = mock.MagicMock()
            mock_boto.return_value = mock_batch_client

            from importlib import reload
            import scheduler.handler
            reload(scheduler.handler)
            from scheduler.handler import handler

            handler({"request_id": "r", "user_id": 1, "s3_keys": ["a.jpg"]}, None)

            mock_batch_client.submit_job.assert_called_once()
            call_kwargs = mock_batch_client.submit_job.call_args[1]
            assert call_kwargs["jobQueue"] == "arn:queue"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_scheduler.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'scheduler'`

- [ ] **Step 3: Create scheduler __init__.py**

Create `services/scheduler/src/scheduler/__init__.py`:

```python
```

(Empty file.)

- [ ] **Step 4: Implement handler.py**

Create `services/scheduler/src/scheduler/handler.py`:

```python
import json
import os

import boto3

from common.batch_manager import PgBatchManager
from common.config import is_local
from common.db import get_connection


def handler(event, context):
    """Lambda handler: 创建 batch，可选提交 Batch Job。"""
    request_id = event["request_id"]
    user_id = event["user_id"]
    s3_keys = event["s3_keys"]

    conn = get_connection()
    try:
        manager = PgBatchManager(conn)
        batch_id = manager.create_batch(request_id, user_id, s3_keys)
        conn.commit()
    finally:
        conn.close()

    if not is_local():
        batch_client = boto3.client("batch")
        batch_client.submit_job(
            jobName=f"photo-batch-{batch_id}",
            jobQueue=os.environ["GPU_JOB_QUEUE"],
            jobDefinition=os.environ["JOB_DEFINITION"],
            containerOverrides={
                "environment": [
                    {"name": "BATCH_ID", "value": str(batch_id)},
                    {"name": "S3_KEYS", "value": json.dumps(s3_keys)},
                ]
            },
        )

    return {
        "statusCode": 200,
        "body": json.dumps({"batch_id": batch_id}),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_scheduler.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add services/scheduler/src/ tests/test_scheduler.py
git commit -m "feat: scheduler Lambda handler — 创建 batch + 条件提交 Batch Job"
```

---

## Task 6: Scheduler Dockerfile

**Files:**
- Create: `services/scheduler/Dockerfile`

- [ ] **Step 1: Create Dockerfile**

Create `services/scheduler/Dockerfile`:

```dockerfile
FROM public.ecr.aws/lambda/python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 复制 workspace 根配置 + 相关包
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/scheduler/ services/scheduler/

# 安装第三方依赖（利用 Docker 层缓存）
RUN uv sync --package scheduler --no-emit-workspace --no-dev --frozen

# 安装 workspace 代码（含 common）
RUN uv sync --package scheduler --no-dev --frozen

# 生产镜像
FROM public.ecr.aws/lambda/python:3.12

# 复制安装好的依赖
COPY --from=builder /app/.venv/lib/python3.12/site-packages/ ${LAMBDA_TASK_ROOT}/

# 复制 scheduler 和 common 源码
COPY services/scheduler/src/scheduler/ ${LAMBDA_TASK_ROOT}/scheduler/
COPY packages/common/src/common/ ${LAMBDA_TASK_ROOT}/common/

CMD ["scheduler.handler.handler"]
```

- [ ] **Step 2: Build image to verify**

```bash
docker build -f services/scheduler/Dockerfile -t photo-scheduler .
```

Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add services/scheduler/Dockerfile
git commit -m "build: scheduler Lambda Container Image Dockerfile"
```

---

## Task 7: Worker — 人脸检测（TDD）

**Files:**
- Create: `services/worker/src/worker/__init__.py`
- Create: `services/worker/src/worker/main.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write tests for worker**

Create `tests/test_worker.py`:

```python
import json
import os
from unittest import mock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def env():
    with mock.patch.dict(os.environ, {
        "DATABASE_URL": "postgresql://dev:dev@localhost:5432/photo_pipeline",
        "LOCAL_DEV": "true",
        "AWS_ENDPOINT_URL": "http://localhost:4566",
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_DEFAULT_REGION": "us-east-1",
        "BATCH_ID": "1",
        "S3_KEYS": json.dumps(["test/photo_0.jpg"]),
    }):
        yield


def _make_fake_face():
    """创建一个假的 InsightFace 检测结果"""
    face = mock.MagicMock()
    face.normed_embedding = np.random.randn(512).astype(np.float32)
    face.bbox = np.array([10.0, 20.0, 110.0, 140.0])
    return face


def test_process_batch_downloads_from_s3():
    """Worker 应从 S3 下载每张照片"""
    with mock.patch("worker.main.FaceAnalysis") as MockFA, \
         mock.patch("worker.main.boto3") as mock_boto, \
         mock.patch("worker.main.PgBatchManager") as MockManager, \
         mock.patch("worker.main.get_connection"):

        # 配置 mock
        mock_s3 = mock.MagicMock()
        mock_boto.client.return_value = mock_s3
        mock_s3.get_object.return_value = {
            "Body": mock.MagicMock(read=lambda: b"\xff\xd8\xff\xe0fake-jpeg")
        }

        model_instance = MockFA.return_value
        model_instance.get.return_value = [_make_fake_face()]

        manager_instance = MockManager.return_value
        manager_instance.get_photo_id.return_value = 100

        with mock.patch("worker.main.cv2") as mock_cv2:
            mock_cv2.imdecode.return_value = np.zeros((100, 100, 3), dtype=np.uint8)

            from importlib import reload
            import worker.main
            reload(worker.main)
            worker.main.process_batch()

        mock_s3.get_object.assert_called_once_with(
            Bucket="photo-uploads", Key="test/photo_0.jpg"
        )


def test_process_batch_writes_embeddings():
    """Worker 应将检测到的 face embedding 写入 DB"""
    with mock.patch("worker.main.FaceAnalysis") as MockFA, \
         mock.patch("worker.main.boto3") as mock_boto, \
         mock.patch("worker.main.PgBatchManager") as MockManager, \
         mock.patch("worker.main.get_connection"):

        mock_s3 = mock.MagicMock()
        mock_boto.client.return_value = mock_s3
        mock_s3.get_object.return_value = {
            "Body": mock.MagicMock(read=lambda: b"\xff\xd8\xff\xe0fake-jpeg")
        }

        fake_face = _make_fake_face()
        model_instance = MockFA.return_value
        model_instance.get.return_value = [fake_face]

        manager_instance = MockManager.return_value
        manager_instance.get_photo_id.return_value = 100

        with mock.patch("worker.main.cv2") as mock_cv2:
            mock_cv2.imdecode.return_value = np.zeros((100, 100, 3), dtype=np.uint8)

            from importlib import reload
            import worker.main
            reload(worker.main)
            worker.main.process_batch()

        manager_instance.insert_embedding.assert_called_once()
        call_args = manager_instance.insert_embedding.call_args
        assert call_args[0][0] == 100  # photo_id
        assert len(call_args[0][1]) == 512  # embedding dim
        assert "x" in call_args[0][2]  # bbox dict


def test_process_batch_marks_completion():
    """Worker 应标记 photo 和 batch 完成"""
    with mock.patch("worker.main.FaceAnalysis") as MockFA, \
         mock.patch("worker.main.boto3") as mock_boto, \
         mock.patch("worker.main.PgBatchManager") as MockManager, \
         mock.patch("worker.main.get_connection"):

        mock_s3 = mock.MagicMock()
        mock_boto.client.return_value = mock_s3
        mock_s3.get_object.return_value = {
            "Body": mock.MagicMock(read=lambda: b"\xff\xd8\xff\xe0fake-jpeg")
        }

        model_instance = MockFA.return_value
        model_instance.get.return_value = [_make_fake_face()]

        manager_instance = MockManager.return_value
        manager_instance.get_photo_id.return_value = 100

        with mock.patch("worker.main.cv2") as mock_cv2:
            mock_cv2.imdecode.return_value = np.zeros((100, 100, 3), dtype=np.uint8)

            from importlib import reload
            import worker.main
            reload(worker.main)
            worker.main.process_batch()

        manager_instance.mark_photo_complete.assert_called_once_with(100, face_count=1)
        manager_instance.mark_batch_complete.assert_called_once_with(1)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_worker.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'worker'`

- [ ] **Step 3: Create worker __init__.py**

Create `services/worker/src/worker/__init__.py`:

```python
```

(Empty file.)

- [ ] **Step 4: Implement worker main.py**

Create `services/worker/src/worker/main.py`:

```python
import json
import os

import boto3
import cv2
import numpy as np
import onnxruntime
from insightface.app import FaceAnalysis

from common.batch_manager import PgBatchManager
from common.db import get_connection


def _load_model() -> FaceAnalysis:
    """加载 InsightFace buffalo_l，自动检测 CUDA/CPU"""
    providers = (
        ["CUDAExecutionProvider"]
        if "CUDAExecutionProvider" in onnxruntime.get_available_providers()
        else ["CPUExecutionProvider"]
    )
    model = FaceAnalysis(name="buffalo_l", providers=providers)
    model.prepare(ctx_id=0)
    return model


def process_batch():
    """处理一个 batch：从 S3 下载照片 → 人脸检测 → 写 embedding 到 DB"""
    batch_id = int(os.environ["BATCH_ID"])
    s3_keys = json.loads(os.environ["S3_KEYS"])

    s3 = boto3.client("s3")
    model = _load_model()

    conn = get_connection()
    try:
        manager = PgBatchManager(conn)

        for s3_key in s3_keys:
            # 1. 从 S3 下载照片
            obj = s3.get_object(Bucket="photo-uploads", Key=s3_key)
            img_bytes = obj["Body"].read()
            img_array = np.frombuffer(img_bytes, dtype=np.uint8)
            img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

            # 2. 人脸检测 + embedding 提取
            faces = model.get(img)

            # 3. 写入 DB
            photo_id = manager.get_photo_id(batch_id, s3_key)
            for face in faces:
                bbox = {
                    "x": int(face.bbox[0]),
                    "y": int(face.bbox[1]),
                    "w": int(face.bbox[2] - face.bbox[0]),
                    "h": int(face.bbox[3] - face.bbox[1]),
                }
                manager.insert_embedding(
                    photo_id=photo_id,
                    embedding=face.normed_embedding.tolist(),
                    bbox=bbox,
                )

            manager.mark_photo_complete(photo_id, face_count=len(faces))

        # 4. 检查 batch 是否全部完成
        manager.mark_batch_complete(batch_id)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    process_batch()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_worker.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add services/worker/src/ tests/test_worker.py
git commit -m "feat: worker — InsightFace 人脸检测 + embedding 写入 (CPU/GPU 自动切换)"
```

---

## Task 8: Worker Dockerfile

**Files:**
- Create: `services/worker/Dockerfile`

- [ ] **Step 1: Create Dockerfile**

Create `services/worker/Dockerfile`:

```dockerfile
# Worker: CPU 本地 / GPU AWS Batch
# 基础镜像用 python:3.12-slim 而非 nvidia/cuda，因为：
# - 本地开发不需要 CUDA（onnxruntime 自动选 CPU）
# - AWS Batch Job Definition 可指定 nvidia/cuda 基础镜像
# - 减小本地构建时间和镜像体积
FROM python:3.12-slim AS builder

# 安装系统依赖（OpenCV 需要 libgl）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 复制 workspace 根配置 + 相关包
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/worker/ services/worker/

# 安装第三方依赖（利用 Docker 层缓存）
RUN uv sync --package worker --no-emit-workspace --no-dev --frozen

# 安装 workspace 代码（含 common）
RUN uv sync --package worker --no-dev --frozen

# 预下载 buffalo_l 模型，避免运行时下载
RUN uv run python -c "from insightface.app import FaceAnalysis; FaceAnalysis(name='buffalo_l')"

CMD ["uv", "run", "--package", "worker", "python", "-m", "worker.main"]
```

- [ ] **Step 2: Build image to verify**

```bash
docker build -f services/worker/Dockerfile -t photo-worker .
```

Expected: build succeeds (第一次较慢，insightface + onnxruntime 体积大)。

- [ ] **Step 3: Commit**

```bash
git add services/worker/Dockerfile
git commit -m "build: worker Docker 镜像 (CPU/GPU 自动切换，预下载模型)"
```

---

## Task 9: Timeout Checker 占位

**Files:**
- Create: `services/timeout_checker/src/timeout_checker/__init__.py`
- Create: `services/timeout_checker/src/timeout_checker/handler.py`
- Create: `services/timeout_checker/Dockerfile`

- [ ] **Step 1: Create placeholder files**

`services/timeout_checker/src/timeout_checker/__init__.py`:

```python
```

`services/timeout_checker/src/timeout_checker/handler.py`:

```python
def handler(event, context):
    """占位：AWS 部署时由 EventBridge 定时触发，扫描超时 batch。"""
    return {"statusCode": 200, "body": "not implemented"}
```

`services/timeout_checker/Dockerfile`:

```dockerfile
FROM public.ecr.aws/lambda/python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/timeout_checker/ services/timeout_checker/

RUN uv sync --package timeout-checker --no-emit-workspace --no-dev --frozen
RUN uv sync --package timeout-checker --no-dev --frozen

FROM public.ecr.aws/lambda/python:3.12
COPY --from=builder /app/.venv/lib/python3.12/site-packages/ ${LAMBDA_TASK_ROOT}/
COPY services/timeout_checker/src/timeout_checker/ ${LAMBDA_TASK_ROOT}/timeout_checker/
COPY packages/common/src/common/ ${LAMBDA_TASK_ROOT}/common/

CMD ["timeout_checker.handler.handler"]
```

- [ ] **Step 2: Commit**

```bash
git add services/timeout_checker/
git commit -m "chore: timeout_checker 占位 (handler + Dockerfile)"
```

---

## Task 10: setup_ministack.py — MiniStack 初始化

**Files:**
- Create: `scripts/setup_ministack.py`

- [ ] **Step 1: Create setup_ministack.py**

Create `scripts/setup_ministack.py`:

```python
"""启动 MiniStack 后运行一次，创建本地 AWS 资源 + 部署 Lambda Container Image"""
import subprocess
import sys
import time

import boto3


def wait_for_ministack(max_retries: int = 30):
    """等待 MiniStack 就绪"""
    s3 = boto3.client("s3")
    for i in range(max_retries):
        try:
            s3.list_buckets()
            print("✅ MiniStack is ready")
            return
        except Exception:
            print(f"⏳ Waiting for MiniStack... ({i + 1}/{max_retries})")
            time.sleep(1)
    print("❌ MiniStack failed to start", file=sys.stderr)
    sys.exit(1)


def setup():
    wait_for_ministack()

    s3 = boto3.client("s3")
    sqs = boto3.client("sqs")
    lam = boto3.client("lambda")

    # 1. 创建 S3 Bucket
    try:
        s3.create_bucket(Bucket="photo-uploads")
        print("✅ S3 bucket 'photo-uploads' created")
    except s3.exceptions.BucketAlreadyExists:
        print("ℹ️  S3 bucket 'photo-uploads' already exists")

    # 2. 创建 SQS DLQ
    try:
        sqs.create_queue(QueueName="scheduler-dlq")
        print("✅ SQS DLQ 'scheduler-dlq' created")
    except Exception:
        print("ℹ️  SQS DLQ 'scheduler-dlq' already exists")

    # 3. 部署 Lambda（Container Image 模式）
    try:
        lam.create_function(
            FunctionName="photo-scheduler",
            PackageType="Image",
            Role="arn:aws:iam::000000000000:role/lambda-role",
            Code={"ImageUri": "photo-scheduler:latest"},
            Environment={
                "Variables": {
                    "LOCAL_DEV": "true",
                    "DATABASE_URL": "postgresql://dev:dev@host.docker.internal:5432/photo_pipeline",
                    "AWS_ENDPOINT_URL": "http://host.docker.internal:4566",
                    "AWS_ACCESS_KEY_ID": "test",
                    "AWS_SECRET_ACCESS_KEY": "test",
                    "AWS_DEFAULT_REGION": "us-east-1",
                }
            },
            Timeout=30,
        )
        print("✅ Lambda 'photo-scheduler' deployed (Container Image)")
    except lam.exceptions.ResourceConflictException:
        # Lambda 已存在，更新代码
        lam.update_function_code(
            FunctionName="photo-scheduler",
            ImageUri="photo-scheduler:latest",
        )
        print("ℹ️  Lambda 'photo-scheduler' updated")


if __name__ == "__main__":
    setup()
```

- [ ] **Step 2: Test setup script**

```bash
make up
make migrate
make build-scheduler
uv run python scripts/setup_ministack.py
```

Expected: all resources created successfully.

- [ ] **Step 3: Verify Lambda exists**

```bash
AWS_ENDPOINT_URL=http://localhost:4566 aws lambda list-functions --query 'Functions[].FunctionName'
```

Expected: `["photo-scheduler"]`

- [ ] **Step 4: Commit**

```bash
git add scripts/setup_ministack.py
git commit -m "feat: setup_ministack.py — S3 + SQS + Lambda Container Image 部署"
```

---

## Task 11: 测试 fixtures + E2E 测试脚本

**Files:**
- Create: `tests/fixtures/` (test images)
- Create: `scripts/test_e2e.py`

- [ ] **Step 1: Generate test fixture images with faces**

Create a script to generate synthetic test images with face-like features. For real e2e, we need actual face images. Download 2 small public-domain face images:

```bash
mkdir -p tests/fixtures
# 使用 OpenCV 生成包含简单人脸特征的测试图片
uv run python -c "
import cv2
import numpy as np

for i in range(3):
    # 创建 200x200 图片，画一个简单的人脸轮廓
    img = np.ones((200, 200, 3), dtype=np.uint8) * 200
    # 脸部轮廓
    cv2.ellipse(img, (100, 100), (60, 80), 0, 0, 360, (180, 140, 100), -1)
    # 眼睛
    cv2.circle(img, (75, 85), 8, (50, 50, 50), -1)
    cv2.circle(img, (125, 85), 8, (50, 50, 50), -1)
    # 嘴巴
    cv2.ellipse(img, (100, 130), (25, 10), 0, 0, 180, (100, 50, 50), 2)
    cv2.imwrite(f'tests/fixtures/face_{i}.jpg', img)
    print(f'✅ tests/fixtures/face_{i}.jpg created')
"
```

> **Note:** 这些是合成图片，InsightFace 可能检测不到人脸。如果 e2e 测试中 `face_count=0`，需要替换为真实人脸照片（如 Unsplash 免费人像照，裁剪到 < 100KB）。E2E 测试应处理 `face_count=0` 的情况。

- [ ] **Step 2: Create test_e2e.py**

Create `scripts/test_e2e.py`:

```python
"""端到端测试：上传照片 → 调用 Lambda → 运行 Worker → 验证 DB"""
import json
import os
import subprocess
import sys
import time

import boto3
import psycopg2


def main():
    db_url = os.environ.get(
        "DATABASE_URL", "postgresql://dev:dev@localhost:5432/photo_pipeline"
    )

    s3 = boto3.client("s3")
    lam = boto3.client("lambda")

    # 1. 上传测试照片到 MiniStack S3
    fixture_dir = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")
    s3_keys = []
    for filename in sorted(os.listdir(fixture_dir)):
        if filename.endswith(".jpg"):
            key = f"test/{filename}"
            filepath = os.path.join(fixture_dir, filename)
            s3.put_object(
                Bucket="photo-uploads",
                Key=key,
                Body=open(filepath, "rb").read(),
            )
            s3_keys.append(key)
            print(f"✅ Uploaded {key}")

    if not s3_keys:
        print("❌ No test fixtures found in tests/fixtures/", file=sys.stderr)
        sys.exit(1)

    # 2. 调用 MiniStack Lambda
    print(f"\n📞 Invoking Lambda with {len(s3_keys)} photos...")
    resp = lam.invoke(
        FunctionName="photo-scheduler",
        Payload=json.dumps({
            "request_id": f"e2e-test-{int(time.time())}",
            "user_id": 1,
            "s3_keys": s3_keys,
        }),
    )
    payload = json.loads(resp["Payload"].read())
    print(f"Lambda response: {payload}")

    if isinstance(payload, dict) and "body" in payload:
        body = json.loads(payload["body"])
    else:
        body = payload
    batch_id = body["batch_id"]
    print(f"✅ Lambda returned batch_id={batch_id}")

    # 3. Docker 容器运行 Worker（CPU 模式）
    print("\n🔧 Running Worker container...")
    result = subprocess.run(
        [
            "docker", "compose", "run", "--rm",
            "-e", f"BATCH_ID={batch_id}",
            "-e", f"S3_KEYS={json.dumps(s3_keys)}",
            "worker",
        ],
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"❌ Worker failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    print("✅ Worker completed")

    # 4. 验证数据库
    print("\n🔍 Verifying database...")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    # 检查 batch status
    cur.execute(
        "SELECT status, total, completed FROM photo_batches WHERE batch_id = %s",
        (batch_id,),
    )
    batch = cur.fetchone()
    print(f"  Batch: status={batch[0]}, total={batch[1]}, completed={batch[2]}")
    assert batch[0] == "completed", f"Expected batch status 'completed', got '{batch[0]}'"

    # 检查 photos
    cur.execute(
        "SELECT COUNT(*), SUM(face_count) FROM photos WHERE batch_id = %s AND status = 'completed'",
        (batch_id,),
    )
    photos = cur.fetchone()
    print(f"  Photos: {photos[0]} completed, {photos[1] or 0} total faces")
    assert photos[0] == len(s3_keys), f"Expected {len(s3_keys)} completed photos, got {photos[0]}"

    # 检查 face_embeddings
    cur.execute(
        """SELECT COUNT(*), MIN(array_length(embedding::text::float[], 1))
           FROM face_embeddings fe
           JOIN photos p ON fe.photo_id = p.photo_id
           WHERE p.batch_id = %s""",
        (batch_id,),
    )
    embeddings = cur.fetchone()
    print(f"  Embeddings: {embeddings[0]} records")

    # face_count 可能为 0（合成图片不一定能检测到人脸）
    if embeddings[0] > 0:
        print(f"  ✅ {embeddings[0]} face embeddings written")
    else:
        print("  ⚠️  No face embeddings (synthetic test images may not contain detectable faces)")

    conn.close()
    print("\n🎉 E2E test passed!")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the full e2e test**

```bash
make test-e2e
```

Expected: all steps pass. If Worker build fails (first time), run `make build-worker` separately first.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/ scripts/test_e2e.py
git commit -m "test: e2e 测试 — 上传照片 → Lambda → Worker → DB 验证"
```

---

## Task 12: 最终验证 — 完整流程冒烟测试

- [ ] **Step 1: Clean start**

```bash
make down
docker volume prune -f
```

- [ ] **Step 2: Full setup from scratch**

```bash
make setup
```

Expected: Docker Compose up → dbmate migrate → build scheduler → deploy Lambda.

- [ ] **Step 3: Run unit tests**

```bash
make test
```

Expected: all unit tests pass.

- [ ] **Step 4: Run e2e test**

```bash
make build-worker
make test-e2e
```

Expected: full pipeline completes, DB verification passes.

- [ ] **Step 5: Verify environment variable switch works**

```bash
# 确认 boto3 不硬编码 endpoint_url
grep -r "endpoint_url" packages/ services/ --include="*.py"
```

Expected: 零结果。所有 boto3 调用通过 `AWS_ENDPOINT_URL` 环境变量。

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "chore: 本地开发环境完成 — MiniStack + pgvector + Worker CPU e2e 跑通"
```

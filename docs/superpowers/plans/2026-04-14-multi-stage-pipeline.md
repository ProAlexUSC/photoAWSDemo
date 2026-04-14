# 多阶段 Pipeline 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Step Functions 编排多阶段照片处理 Pipeline（Worker → Tagger → VLM → Complete），本地 MiniStack 完整跑通 e2e 测试。

**Architecture:** Scheduler 启动 Step Functions 状态机，Worker 通过 ECS RunTask.sync（本地）/ Batch submitJob.sync（AWS）处理所有照片，然后 Map state 并行调用 Tagger 和 VLM Lambda 逐张处理，最后 MarkComplete 标记 batch 完成。

**Tech Stack:** Python 3.12, uv workspace, MiniStack (Step Functions + ECS + Lambda), pgvector, LangSmith, boto3

**Spec:** `docs/superpowers/specs/2026-04-14-multi-stage-pipeline-design.md`

---

## Task 1: DB Migration — 添加 tags 和 vlm_result 列

**Files:**
- Create: `migrations/002_add_tags_vlm.sql`

- [ ] **Step 1: 写 migration 文件**

```sql
-- migrate:up
ALTER TABLE photos ADD COLUMN IF NOT EXISTS tags JSONB;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS vlm_result JSONB;

-- migrate:down
ALTER TABLE photos DROP COLUMN IF EXISTS vlm_result;
ALTER TABLE photos DROP COLUMN IF EXISTS tags;
```

- [ ] **Step 2: 执行 migration**

Run: `dbmate -d ./migrations up`
Expected: `Applied: 002_add_tags_vlm.sql`

- [ ] **Step 3: 验证列存在**

Run: `docker exec photoaws-postgres-1 psql -U dev photo_pipeline -c "\d photos"`
Expected: 输出包含 `tags | jsonb` 和 `vlm_result | jsonb`

- [ ] **Step 4: Commit**

```bash
git add migrations/002_add_tags_vlm.sql
git commit -m "feat: migration 002 添加 photos.tags 和 photos.vlm_result 列"
```

---

## Task 2: 新增 get_photo_ids 服务

**Files:**
- Create: `services/get_photo_ids/pyproject.toml`
- Create: `services/get_photo_ids/src/get_photo_ids/__init__.py`
- Create: `services/get_photo_ids/src/get_photo_ids/handler.py`
- Create: `services/get_photo_ids/Dockerfile`
- Create: `tests/test_get_photo_ids.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_get_photo_ids.py
from unittest import mock

import psycopg2
import pytest


def test_get_photo_ids_returns_list():
    from get_photo_ids.handler import handler

    with mock.patch("get_photo_ids.handler.get_connection") as mock_conn:
        conn = mock.MagicMock()
        cur = mock.MagicMock()
        cur.fetchall.return_value = [(1,), (2,), (3,)]
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        result = handler({"batch_id": 1}, None)

        assert result == {"batch_id": 1, "photo_ids": [1, 2, 3]}
        cur.execute.assert_called_once()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_get_photo_ids.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'get_photo_ids'`

- [ ] **Step 3: 创建 pyproject.toml**

```toml
# services/get_photo_ids/pyproject.toml
[project]
name = "get-photo-ids"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["common", "boto3>=1.28.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/get_photo_ids"]

[tool.uv.sources]
common = { workspace = true }
```

- [ ] **Step 4: 创建 handler**

```python
# services/get_photo_ids/src/get_photo_ids/__init__.py
```

```python
# services/get_photo_ids/src/get_photo_ids/handler.py
import json

from common.db import get_connection


def handler(event, context):
    batch_id = event["batch_id"]
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT photo_id FROM photos WHERE batch_id = %s ORDER BY photo_id", (batch_id,))
        photo_ids = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
    return {"batch_id": batch_id, "photo_ids": photo_ids}
```

- [ ] **Step 5: 创建 Dockerfile**

```dockerfile
# services/get_photo_ids/Dockerfile
FROM public.ecr.aws/lambda/python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/get_photo_ids/ services/get_photo_ids/

RUN uv sync --package get-photo-ids --no-install-workspace --no-dev --frozen
RUN uv sync --package get-photo-ids --no-dev --frozen

FROM public.ecr.aws/lambda/python:3.12

COPY --from=builder /app/.venv/lib/python3.12/site-packages/ ${LAMBDA_TASK_ROOT}/

COPY services/get_photo_ids/src/get_photo_ids/ ${LAMBDA_TASK_ROOT}/get_photo_ids/
COPY packages/common/src/common/ ${LAMBDA_TASK_ROOT}/common/

CMD ["get_photo_ids.handler.handler"]
```

- [ ] **Step 6: uv sync 并运行测试**

Run: `uv sync --all-packages --dev && uv run pytest tests/test_get_photo_ids.py -v`
Expected: PASS

- [ ] **Step 7: Ruff lint**

Run: `uv run ruff check tests/test_get_photo_ids.py services/get_photo_ids/`
Expected: All checks passed

- [ ] **Step 8: Commit**

```bash
git add services/get_photo_ids/ tests/test_get_photo_ids.py
git commit -m "feat: 新增 get_photo_ids Lambda（从 DB 查 photo_ids 供 Map state 使用）"
```

---

## Task 3: 新增 tagger 服务（Stage 2 mock）

**Files:**
- Create: `services/tagger/pyproject.toml`
- Create: `services/tagger/src/tagger/__init__.py`
- Create: `services/tagger/src/tagger/handler.py`
- Create: `services/tagger/Dockerfile`
- Create: `tests/test_tagger.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_tagger.py
from unittest import mock


def test_tagger_writes_tags_to_db():
    from tagger.handler import handler

    with mock.patch("tagger.handler.get_connection") as mock_conn:
        conn = mock.MagicMock()
        cur = mock.MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        result = handler({"photo_id": 42}, None)

        assert result["photo_id"] == 42
        assert result["status"] == "tagged"
        cur.execute.assert_called_once()
        args = cur.execute.call_args[0]
        assert "UPDATE photos SET tags" in args[0]
        assert args[1][1] == 42
        conn.commit.assert_called_once()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_tagger.py -v`
Expected: FAIL

- [ ] **Step 3: 创建 pyproject.toml**

```toml
# services/tagger/pyproject.toml
[project]
name = "tagger"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["common", "boto3>=1.28.0", "langsmith>=0.3.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/tagger"]

[tool.uv.sources]
common = { workspace = true }
```

- [ ] **Step 4: 创建 handler**

```python
# services/tagger/src/tagger/__init__.py
```

```python
# services/tagger/src/tagger/handler.py
import json

from langsmith import traceable

from common.db import get_connection


@traceable(name="stage2_tag_photo")
def handler(event, context):
    photo_id = event["photo_id"]

    tags = {"scene": "outdoor", "objects": ["person", "tree"], "mood": "happy"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE photos SET tags = %s WHERE photo_id = %s",
            (json.dumps(tags), photo_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {"photo_id": photo_id, "status": "tagged"}
```

- [ ] **Step 5: 创建 Dockerfile**（和 get_photo_ids 同模板，替换 service name）

```dockerfile
# services/tagger/Dockerfile
FROM public.ecr.aws/lambda/python:3.12 AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/tagger/ services/tagger/
RUN uv sync --package tagger --no-install-workspace --no-dev --frozen
RUN uv sync --package tagger --no-dev --frozen

FROM public.ecr.aws/lambda/python:3.12
COPY --from=builder /app/.venv/lib/python3.12/site-packages/ ${LAMBDA_TASK_ROOT}/
COPY services/tagger/src/tagger/ ${LAMBDA_TASK_ROOT}/tagger/
COPY packages/common/src/common/ ${LAMBDA_TASK_ROOT}/common/
CMD ["tagger.handler.handler"]
```

- [ ] **Step 6: uv sync + 测试 + lint**

Run: `uv sync --all-packages --dev && uv run pytest tests/test_tagger.py -v && uv run ruff check services/tagger/ tests/test_tagger.py`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add services/tagger/ tests/test_tagger.py
git commit -m "feat: 新增 tagger Lambda（Stage 2 mock 打标）"
```

---

## Task 4: 新增 vlm_extractor 服务（Stage 3 mock）

**Files:**
- Create: `services/vlm_extractor/pyproject.toml`
- Create: `services/vlm_extractor/src/vlm_extractor/__init__.py`
- Create: `services/vlm_extractor/src/vlm_extractor/handler.py`
- Create: `services/vlm_extractor/Dockerfile`
- Create: `tests/test_vlm_extractor.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_vlm_extractor.py
from unittest import mock


def test_vlm_extractor_writes_result_to_db():
    from vlm_extractor.handler import handler

    with mock.patch("vlm_extractor.handler.get_connection") as mock_conn:
        conn = mock.MagicMock()
        cur = mock.MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        result = handler({"photo_id": 42}, None)

        assert result["photo_id"] == 42
        assert result["status"] == "extracted"
        cur.execute.assert_called_once()
        args = cur.execute.call_args[0]
        assert "UPDATE photos SET vlm_result" in args[0]
        assert args[1][1] == 42
        conn.commit.assert_called_once()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_vlm_extractor.py -v`
Expected: FAIL

- [ ] **Step 3: 创建 pyproject.toml**

```toml
# services/vlm_extractor/pyproject.toml
[project]
name = "vlm-extractor"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["common", "boto3>=1.28.0", "langsmith>=0.3.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/vlm_extractor"]

[tool.uv.sources]
common = { workspace = true }
```

- [ ] **Step 4: 创建 handler + Dockerfile**

handler 结构和 tagger 相同，mock 数据不同：

```python
# services/vlm_extractor/src/vlm_extractor/handler.py
import json

from langsmith import traceable

from common.db import get_connection


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
        cur.execute(
            "UPDATE photos SET vlm_result = %s WHERE photo_id = %s",
            (json.dumps(vlm_result), photo_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {"photo_id": photo_id, "status": "extracted"}
```

Dockerfile 同 tagger 模板，替换 `tagger` → `vlm_extractor`，`vlm-extractor`。

- [ ] **Step 5: uv sync + 测试 + lint**

Run: `uv sync --all-packages --dev && uv run pytest tests/test_vlm_extractor.py -v && uv run ruff check services/vlm_extractor/ tests/test_vlm_extractor.py`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add services/vlm_extractor/ tests/test_vlm_extractor.py
git commit -m "feat: 新增 vlm_extractor Lambda（Stage 3 mock VLM 提取）"
```

---

## Task 5: 新增 mark_complete 服务

**Files:**
- Create: `services/mark_complete/pyproject.toml`
- Create: `services/mark_complete/src/mark_complete/__init__.py`
- Create: `services/mark_complete/src/mark_complete/handler.py`
- Create: `services/mark_complete/Dockerfile`
- Create: `tests/test_mark_complete.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mark_complete.py
from unittest import mock


def test_mark_complete_sets_batch_completed():
    from mark_complete.handler import handler

    with (
        mock.patch("mark_complete.handler.get_connection") as mock_conn,
        mock.patch("mark_complete.handler.PgBatchManager") as MockManager,
    ):
        conn = mock.MagicMock()
        mock_conn.return_value = conn
        instance = MockManager.return_value

        result = handler({"batch_id": 1}, None)

        assert result == {"batch_id": 1, "status": "completed"}
        instance.mark_batch_complete.assert_called_once_with(1)
        conn.commit.assert_called_once()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_mark_complete.py -v`
Expected: FAIL

- [ ] **Step 3: 创建 pyproject.toml + handler + Dockerfile**

```python
# services/mark_complete/src/mark_complete/handler.py
from common.batch_manager import PgBatchManager
from common.db import get_connection


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

pyproject.toml 同 get_photo_ids 模板（name=`mark-complete`，packages=`["src/mark_complete"]`）。Dockerfile 同模板。

- [ ] **Step 4: uv sync + 测试 + lint**

Run: `uv sync --all-packages --dev && uv run pytest tests/test_mark_complete.py -v && uv run ruff check services/mark_complete/ tests/test_mark_complete.py`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add services/mark_complete/ tests/test_mark_complete.py
git commit -m "feat: 新增 mark_complete Lambda（标记 batch 完成）"
```

---

## Task 6: 删除 timeout_checker placeholder

**Files:**
- Delete: `services/timeout_checker/` (整个目录)

Step Functions 内置 TimeoutSeconds 替代了 timeout_checker 的职责。

- [ ] **Step 1: 删除并更新 pyrightconfig**

```bash
rm -rf services/timeout_checker/
```

从 `pyrightconfig.json` 的 `include` 和 `extraPaths` 中移除 `services/timeout_checker/src`。

- [ ] **Step 2: uv sync 确认无报错**

Run: `uv sync --all-packages --dev`
Expected: 无 timeout_checker 相关错误

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: 删除 timeout_checker placeholder（Step Functions TimeoutSeconds 替代）"
```

---

## Task 7: 状态机定义

**Files:**
- Create: `state-machines/pipeline-local.json`
- Create: `state-machines/pipeline-aws.json`

- [ ] **Step 1: 创建本地状态机定义**

创建 `state-machines/pipeline-local.json`，内容如 spec 第 5 节所定义。RunWorker 使用 `arn:aws:states:::ecs:runTask.sync`，含 `TimeoutSeconds: 600`。TagPhotos 和 VLMExtract 使用 Map state（`MaxConcurrency: 10`）。

- [ ] **Step 2: 创建 AWS 状态机定义**

复制 `pipeline-local.json` → `pipeline-aws.json`，仅替换 RunWorker state：
- Resource: `arn:aws:states:::batch:submitJob.sync`
- Parameters: `JobName`, `JobQueue`, `JobDefinition`, `ContainerOverrides`

- [ ] **Step 3: 验证 JSON 合法**

Run: `python -c "import json; json.load(open('state-machines/pipeline-local.json')); json.load(open('state-machines/pipeline-aws.json')); print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add state-machines/
git commit -m "feat: Step Functions 状态机定义（local ECS + AWS Batch）"
```

---

## Task 8: 修改 Scheduler — 启动状态机

**Files:**
- Modify: `services/scheduler/src/scheduler/handler.py`
- Modify: `tests/test_scheduler.py`

- [ ] **Step 1: 更新 scheduler 测试**

替换现有的 `test_handler_skips_batch_submit_when_local` 和 `test_handler_submits_batch_job_when_not_local` 为统一的状态机启动测试：

```python
# tests/test_scheduler.py — 替换后的完整内容
import json
import os
from importlib import reload
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def env():
    with mock.patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://dev:dev@localhost:5432/photo_pipeline",
            "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:000000000000:stateMachine:photo-pipeline",
        },
    ):
        yield


def _reload_handler():
    import scheduler.handler

    reload(scheduler.handler)
    return scheduler.handler


def test_handler_creates_batch_and_starts_execution():
    mod = _reload_handler()
    with (
        mock.patch.object(mod, "PgBatchManager") as MockManager,
        mock.patch.object(mod, "get_connection") as mock_conn,
        mock.patch.object(mod, "boto3") as mock_boto,
    ):
        instance = MockManager.return_value
        instance.create_batch.return_value = 42
        mock_conn.return_value = mock.MagicMock()
        mock_sfn = mock.MagicMock()
        mock_boto.client.return_value = mock_sfn

        event = {"request_id": "test-001", "user_id": 1, "s3_keys": ["a.jpg", "b.jpg"]}
        result = mod.handler(event, None)

        body = json.loads(result["body"])
        assert result["statusCode"] == 200
        assert body["batch_id"] == 42
        instance.create_batch.assert_called_once_with("test-001", 1, ["a.jpg", "b.jpg"])
        mock_sfn.start_execution.assert_called_once()
        call_kwargs = mock_sfn.start_execution.call_args[1]
        assert "batch-42" in call_kwargs["name"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: FAIL — scheduler 仍调用 `batch_client.submit_job`

- [ ] **Step 3: 修改 handler**

```python
# services/scheduler/src/scheduler/handler.py
import json
import os

import boto3

from common.batch_manager import PgBatchManager
from common.db import get_connection


def handler(event, context):
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

    sfn = boto3.client("stepfunctions")
    sfn.start_execution(
        stateMachineArn=os.environ["STATE_MACHINE_ARN"],
        name=f"batch-{batch_id}",
        input=json.dumps({"batch_id": batch_id, "s3_keys": s3_keys}),
    )

    return {
        "statusCode": 200,
        "body": json.dumps({"batch_id": batch_id}),
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: PASS

- [ ] **Step 5: Ruff lint**

Run: `uv run ruff check services/scheduler/ tests/test_scheduler.py && uv run ruff format --check services/scheduler/ tests/test_scheduler.py`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add services/scheduler/ tests/test_scheduler.py
git commit -m "feat: Scheduler 改为启动 Step Functions 状态机（移除 is_local 分支）"
```

---

## Task 9: 更新依赖 + conftest

**Files:**
- Modify: `pyproject.toml` (root)
- Modify: `tests/conftest.py`
- Modify: `pyrightconfig.json`

- [ ] **Step 1: root pyproject.toml 添加 langsmith dev 依赖**

```toml
[dependency-groups]
dev = [
    "numpy>=2.4.4",
    "pytest>=8.0.0",
    "ruff>=0.11.0",
    "langsmith>=0.3.0",
]
```

- [ ] **Step 2: conftest.py 添加新服务 src 路径**

```python
# tests/conftest.py
import sys
from pathlib import Path

root = Path(__file__).parent.parent
for service_dir in (root / "services").iterdir():
    src = service_dir / "src"
    if src.is_dir():
        sys.path.insert(0, str(src))
sys.path.insert(0, str(root / "packages" / "common" / "src"))
```

- [ ] **Step 3: pyrightconfig.json 更新 include + extraPaths**

添加新服务路径到 `include` 和 `extraPaths`（get_photo_ids, tagger, vlm_extractor, mark_complete），移除 timeout_checker。

- [ ] **Step 4: uv sync + 运行全部测试**

Run: `uv sync --all-packages --dev && uv run pytest tests/ -v`
Expected: 全部通过（现有 + 新增测试）

- [ ] **Step 5: Ruff 全量检查**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/conftest.py pyrightconfig.json uv.lock
git commit -m "chore: 更新依赖（langsmith）+ conftest 自动发现所有 services"
```

---

## Task 10: setup_ministack + Docker 构建

**Files:**
- Modify: `scripts/setup_ministack.py`
- Modify: `Makefile`

- [ ] **Step 1: 更新 setup_ministack.py**

在现有 setup() 中追加：

1. 创建 ECS 集群 `photo-pipeline`
2. 注册 ECS Task Definition `photo-worker`（image: `photo-worker:latest`）
3. 构建并部署 4 个新 Lambda（get-photo-ids, photo-tagger, photo-vlm, photo-mark-complete）
4. 创建 Step Functions 状态机 `photo-pipeline`（读取 `state-machines/pipeline-local.json`，替换 `${...}` 变量）

- [ ] **Step 2: 更新 Makefile**

添加 build targets：

```makefile
build-get-photo-ids:
	docker build -f services/get_photo_ids/Dockerfile -t get-photo-ids .

build-tagger:
	docker build -f services/tagger/Dockerfile -t photo-tagger .

build-vlm:
	docker build -f services/vlm_extractor/Dockerfile -t photo-vlm .

build-mark-complete:
	docker build -f services/mark_complete/Dockerfile -t photo-mark-complete .

build-all: build-scheduler build-worker build-get-photo-ids build-tagger build-vlm build-mark-complete

setup: up migrate build-all
	uv run python scripts/setup_ministack.py
```

- [ ] **Step 3: 测试 `make build-all`**

Run: `make build-all`
Expected: 6 个镜像全部构建成功

- [ ] **Step 4: 测试 `make setup`**

Run: `make setup`
Expected: MiniStack 资源创建成功（S3 + ECS cluster + 5 Lambdas + 状态机）

- [ ] **Step 5: Commit**

```bash
git add scripts/setup_ministack.py Makefile
git commit -m "feat: setup_ministack 创建 ECS 集群 + 新 Lambda + Step Functions 状态机"
```

---

## Task 11: e2e 测试更新

**Files:**
- Modify: `scripts/test_e2e.py`

- [ ] **Step 1: 重写 e2e 测试**

```python
# scripts/test_e2e.py
"""端到端测试：上传照片 → Scheduler 启动状态机 → Step Functions 编排全流程 → 验证 DB"""
import json
import os
import sys
import time

import boto3
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "scheduler", "src"))


def main():
    db_url = os.environ.get("DATABASE_URL", "postgresql://dev:dev@localhost:5433/photo_pipeline")
    s3 = boto3.client("s3")
    sfn = boto3.client("stepfunctions")

    # 1. 上传测试照片到 MiniStack S3
    fixture_dir = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")
    s3_keys = []
    for filename in sorted(os.listdir(fixture_dir)):
        if filename.endswith(".jpg"):
            key = f"test/{filename}"
            filepath = os.path.join(fixture_dir, filename)
            with open(filepath, "rb") as f:
                s3.put_object(Bucket="photo-uploads", Key=key, Body=f.read())
            s3_keys.append(key)
            print(f"✅ Uploaded {key}")

    if not s3_keys:
        print("❌ No test fixtures found", file=sys.stderr)
        sys.exit(1)

    # 2. 调用 Scheduler（启动状态机）
    from scheduler.handler import handler

    request_id = f"e2e-test-{int(time.time())}"
    print(f"\n📞 Calling scheduler with {len(s3_keys)} photos...")
    result = handler({"request_id": request_id, "user_id": 1, "s3_keys": s3_keys}, None)
    body = json.loads(result["body"])
    batch_id = body["batch_id"]
    print(f"✅ Scheduler returned batch_id={batch_id}")

    # 3. 等待状态机执行完成
    print("\n⏳ Waiting for Step Functions execution...")
    executions = sfn.list_executions(
        stateMachineArn=os.environ["STATE_MACHINE_ARN"],
        statusFilter="RUNNING",
    )
    if executions["executions"]:
        execution_arn = executions["executions"][0]["executionArn"]
    else:
        # 可能已经完成了
        executions = sfn.list_executions(
            stateMachineArn=os.environ["STATE_MACHINE_ARN"],
        )
        execution_arn = executions["executions"][0]["executionArn"]

    for _ in range(120):  # 最多等 4 分钟
        resp = sfn.describe_execution(executionArn=execution_arn)
        status = resp["status"]
        if status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
            break
        time.sleep(2)

    print(f"  State machine status: {status}")
    if status != "SUCCEEDED":
        print(f"❌ Execution failed: {resp.get('error', 'unknown')}", file=sys.stderr)
        if "output" in resp:
            print(f"  Output: {resp['output']}", file=sys.stderr)
        sys.exit(1)
    print("✅ State machine completed")

    # 4. 验证数据库
    print("\n🔍 Verifying database...")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    # batch status
    cur.execute("SELECT status FROM photo_batches WHERE batch_id = %s", (batch_id,))
    batch = cur.fetchone()
    print(f"  Batch status: {batch[0]}")
    assert batch[0] == "completed", f"Expected 'completed', got '{batch[0]}'"

    # photos: face_count, tags, vlm_result
    cur.execute(
        "SELECT COUNT(*), SUM(COALESCE(face_count, 0)) "
        "FROM photos WHERE batch_id = %s AND status = 'completed'",
        (batch_id,),
    )
    photos = cur.fetchone()
    print(f"  Photos: {photos[0]} completed, {photos[1] or 0} total faces")
    assert photos[0] == len(s3_keys)

    cur.execute(
        "SELECT COUNT(*) FROM photos WHERE batch_id = %s AND tags IS NOT NULL",
        (batch_id,),
    )
    tagged = cur.fetchone()[0]
    print(f"  Tagged: {tagged}/{len(s3_keys)}")
    assert tagged == len(s3_keys), f"Expected {len(s3_keys)} tagged, got {tagged}"

    cur.execute(
        "SELECT COUNT(*) FROM photos WHERE batch_id = %s AND vlm_result IS NOT NULL",
        (batch_id,),
    )
    vlm_done = cur.fetchone()[0]
    print(f"  VLM extracted: {vlm_done}/{len(s3_keys)}")
    assert vlm_done == len(s3_keys), f"Expected {len(s3_keys)} VLM done, got {vlm_done}"

    # face embeddings
    cur.execute(
        "SELECT COUNT(*) FROM face_embeddings fe "
        "JOIN photos p ON fe.photo_id = p.photo_id "
        "WHERE p.batch_id = %s",
        (batch_id,),
    )
    embedding_count = cur.fetchone()[0]
    if embedding_count > 0:
        print(f"  ✅ {embedding_count} face embeddings written")
    else:
        print("  ⚠️  No face embeddings (test images may not contain detectable faces)")

    conn.close()
    print("\n🎉 E2E test passed! Full pipeline: Worker → Tagger → VLM → Complete")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行 `make test-e2e`**

Run: `make test-e2e`
Expected: 全流程通过，DB 验证 batch completed + tags + vlm_result 非空

- [ ] **Step 3: Commit**

```bash
git add scripts/test_e2e.py
git commit -m "feat: e2e 测试覆盖完整多阶段 Pipeline（SFN → Worker → Tagger → VLM → Complete）"
```

---

## Task 12: 全量验证 + 推送

- [ ] **Step 1: 运行全部单元测试**

Run: `uv run pytest tests/ -v`
Expected: 所有测试通过

- [ ] **Step 2: Ruff 全量检查**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: All pass

- [ ] **Step 3: 运行 e2e 测试**

Run: `make test-e2e`
Expected: 🎉 E2E test passed!

- [ ] **Step 4: 更新 CI（.github/workflows/ci.yml）**

新增服务的单元测试已在 `tests/` 目录下，现有 CI 的 `uv run pytest tests/ -v` 会自动覆盖。需要确认：
- `uv sync --all-packages --dev --frozen` 能安装所有新服务依赖
- 如果 `uv.lock` 有变更，更新并提交

- [ ] **Step 5: 创建 README.md**

```markdown
# photoAWS — 照片处理 Pipeline

基于 AWS 的多阶段照片处理系统：人脸检测 → 打标 → VLM 结构化提取。

## 架构

Step Functions 编排：Worker (ECS/Batch) → Tagger (Lambda) → VLM (Lambda) → MarkComplete (Lambda)

## 本地开发

\`\`\`bash
# 一键启动（MiniStack + PostgreSQL + 资源部署）
make setup

# 运行单元测试
make test

# 运行端到端测试（完整 Pipeline）
make test-e2e
\`\`\`

## 项目结构

- `packages/common/` — 共享代码（DB、BatchManager）
- `services/scheduler/` — Lambda：创建 batch，启动状态机
- `services/worker/` — ECS/Batch：InsightFace 人脸检测 + embedding
- `services/tagger/` — Lambda：照片打标（Stage 2）
- `services/vlm_extractor/` — Lambda：VLM 结构化提取（Stage 3）
- `services/get_photo_ids/` — Lambda：查询 photo_ids 供 Map 使用
- `services/mark_complete/` — Lambda：标记 batch 完成
- `state-machines/` — Step Functions 状态机定义

## 技术栈

Python 3.12 · uv workspace · MiniStack · pgvector · InsightFace · Step Functions · LangSmith
```

- [ ] **Step 6: 提交全部并推送**

```bash
git add -A
git commit -m "feat: 多阶段 Pipeline 完成 — Step Functions + mock LLM + e2e 通过

- 4 个新 Lambda（get_photo_ids, tagger, vlm_extractor, mark_complete）
- Step Functions 状态机编排（Map 并行处理）
- LangSmith @traceable 可观测性
- DB migration 002（tags + vlm_result 列）
- e2e 测试覆盖完整 Pipeline
- README + CI 更新"
git push
```

Expected: CI 全绿

- [ ] **Step 7: 等待 CI 通过**

Run: `gh run watch $(gh run list --limit 1 --json databaseId -q '.[0].databaseId') --exit-status`
Expected: ✓ All steps passed

- [ ] **Step 8: 更新 Obsidian 笔记**

更新 `照片处理 Pipeline 项目工程化` 笔记，反映 Step Functions 编排和新增服务。

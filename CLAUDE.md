# CLAUDE.md

## 项目概述

照片处理 Pipeline：用户上传照片后，通过 Step Functions 编排多阶段 AI 处理（人脸检测 → 打标 → VLM 提取）。本地用 MiniStack 模拟 AWS，Terraform 统一部署。

## 技术栈

- Python 3.12 + uv workspace monorepo
- Terraform/OpenTofu（IaC 统一本地和 AWS 部署）
- MiniStack（本地 AWS 模拟器）
- PostgreSQL + pgvector（人脸 embedding 存储）
- InsightFace buffalo_l（人脸检测 + 512 维 embedding）
- Step Functions（Pipeline 编排）
- Langfuse v4 SDK `@observe`（全 Pipeline 分布式 trace 串联 + 真·父子嵌套）
- X-Ray（AWS Lambda 基础可观测，零代码）

## 常用命令

```bash
make check-deps   # 检查 docker/uv/dbmate/tofu + docker 守护进程 + .env（setup 前自动跑）
make deps         # brew 装 dbmate opentofu uv（docker 需自行装）
make download-models  # 主机预下载 InsightFace buffalo_l ~275MB 到 docker/models/
make setup        # check-deps + up + migrate + build-all + tofu apply
make test-e2e     # 端到端测试（依赖 setup）
make destroy      # tofu destroy + docker compose down -v
make up           # 只启动 Docker Compose
make down         # 只停止 Docker Compose
make migrate      # 只执行数据库迁移
make build-all    # 只构建全部 Docker 镜像
make test         # pytest 单元测试
uv run ruff check .          # lint
uv run ruff format --check . # format check
```

## 项目结构

- `packages/common/` — 共享业务逻辑（BatchManager、DB、Config、Tracing）
- `services/` — 6 个独立服务，各有 pyproject.toml 和 Dockerfile
- `terraform/` — IaC 配置，`local.tfvars` / `aws.tfvars` 切换环境
- `state-machines/` — Step Functions JSON 定义（local 用 ECS，AWS 用 Batch）
- `migrations/` — dbmate SQL 迁移（`-- migrate:up` / `-- migrate:down` 同文件）
- `docker/ministack.Dockerfile` — 自定义 MiniStack 镜像（Alpine base，`ensurepip` 引导后装 psycopg2-binary/boto3/langfuse，走 BFSU PyPI 镜像）
- `docker/models/buffalo_l/` — InsightFace 模型（`.gitignore` 不入库，`make download-models` 预下载）
- `docs/superpowers/specs/` — 设计文档归档

## 可观测性（Langfuse）

- Scheduler `@observe(name="photo_pipeline")` 作为 root span；内部 `get_client().get_current_observation_id()` 捕获自己的 span_id，随 SFN input 传给下游 Lambda
- 分布式 trace 通过两字段传播（见 `packages/common/src/common/tracing.py`）：
  - `langfuse_trace_id` — 确定性 trace_id，`create_trace_id(seed=request_id)` 生成
  - `langfuse_parent_observation_id` — Scheduler 的 span_id
- SFN JSON 每个 Lambda Task 的 `Parameters.Payload` 用 `$$.Execution.Input.xxx` 从执行原始 input 直接取这两字段（不依赖上游 state 的 ResultPath 保留）
- 下游 Lambda `handler()` 里统一调 `run_traced(_op, event, ...)`：自动抽字段 → 用 `@observe` 的 `langfuse_trace_id` / `langfuse_parent_observation_id` kwarg 挂到同一棵树 → 退出前 `flush()` 保证不丢 span
- `@observe` 的 `langfuse_parent_observation_id` kwarg 是 SDK 特性（源码 docstring 有，doc 页未列），让下游 span 真嵌套在 Scheduler 的 `photo_pipeline` 下而非平铺 sibling
- 未配 Langfuse key 时 SDK no-op，零业务影响；`.env` 中设 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` 即可启用

## 开发规范

- 使用中文
- 使用 uv 管理 Python 环境（`uv sync --all-packages --dev`）
- Ruff lint + format（`target-version = "py312"`, `line-length = 100`）
- 数据库迁移用 dbmate（`dbmate -d ./migrations up`）
- 提交前确保 `make test` 和 `uv run ruff check .` 通过

## MiniStack 已知限制

- **Lambda Container Image**：`ImageConfig.Command` 不传给 Docker CMD → 用 Zip 部署
- **ECS RunTask.sync 会 hang**：只在 ECS 里注册一条 RUNNING task 记录，不真跑容器且不会自动转 STOPPED → e2e 脚本 `start_execution` 后主动 `ecs.stop_task` 解锁（`scripts/test_e2e.py:_unlock_ministack_ecs`）；数据靠预跑 `docker compose run worker` 填 DB
- **SFN Activity runtime 不调度**：`create_activity` / `send_task_success` 等 CRUD API 可用，但 state machine 用 Activity Resource 时 `get_activity_task` 永远返回空 token（SFN 不把任务入队）→ 本地不用 Activity 模式
- **SFN `ListStateMachineVersions` 不支持** → `sfn.tf` 本地用 `terraform_data` + `create_sfn.sh` 脚本创建
- **Lambda Warm Workers 依赖**：psycopg2/boto3/langfuse 靠自定义 MiniStack 镜像预装

## 环境配置

- `.env` 基于 `.env.example`（AWS_ENDPOINT_URL、DATABASE_URL、LANGFUSE_* 等）
- Makefile 用 `-include .env` + `export` 加载；若缺失，`check-deps` 自动 `cp .env.example .env`
- `make setup` 会将 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` 通过 `-var` 传给 Terraform 注入 Lambda env
- e2e 测试需要 Docker 运行（MiniStack + PostgreSQL + Worker 容器）

## 国内镜像配置

为规避跨境网络抖动，默认配置了以下镜像（所有都写死在 Dockerfile / uv.lock，不需额外 env）：

- **Debian apt**（worker 镜像）：`deb.debian.org` → `mirrors.tuna.tsinghua.edu.cn`
- **PyPI**（ministack 镜像 + uv.lock）：`mirrors.bfsu.edu.cn/pypi/web/simple`
- **InsightFace buffalo_l 模型**：从 GitHub releases 预下载到 `docker/models/buffalo_l/`（主机一次性 ~275MB），由 `COPY` 进镜像；rebuild 时 Docker layer cache 秒复用
- **`public.ecr.aws`**（Lambda base image）：直连，国内 TLS 偶发 timeout；重试即可

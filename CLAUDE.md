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
- LangSmith `@traceable`（全 Pipeline 分布式 trace 串联）
- X-Ray（AWS Lambda 基础可观测，零代码）

## 常用命令

```bash
make setup        # Docker Compose + migrate + build images + tofu apply
make test         # pytest（19 个单元测试）
make test-e2e     # 6 步端到端测试（需先 make setup）
make destroy      # tofu destroy + docker compose down -v
make up           # 只启动 Docker Compose
make down         # 只停止 Docker Compose
make migrate      # 只执行数据库迁移
make build-all    # 只构建全部 Docker 镜像
uv run ruff check .          # lint
uv run ruff format --check . # format check
```

## 项目结构

- `packages/common/` — 共享业务逻辑（BatchManager、DB、Config、Tracing）
- `services/` — 6 个独立服务，各有 pyproject.toml 和 Dockerfile
- `terraform/` — IaC 配置，`local.tfvars` / `aws.tfvars` 切换环境
- `state-machines/` — Step Functions JSON 定义（local 用 ECS，AWS 用 Batch）
- `migrations/` — dbmate SQL 迁移（`-- migrate:up` / `-- migrate:down` 同文件）
- `docker/ministack.Dockerfile` — 自定义 MiniStack 镜像（预装 psycopg2/boto3/langsmith）

## 可观测性

- Scheduler `@traceable(name="photo_pipeline")` 创建 parent trace
- 下游 Lambda 通过 `parent_trace_from(event)` 恢复 parent context，`@traceable` 创建 child span
- trace context 通过 event payload 的 `langsmith_trace_context` 字段传播
- 本地 Lambda `LANGSMITH_TRACING=false`（避免 MiniStack Warm Worker 重复），由 `parent_trace_from` 临时启用
- AWS Lambda `LANGSMITH_TRACING=true`（原生生效）
- `.env` 中设 `LANGSMITH_API_KEY` 即可启用，`make setup` 自动注入

## 开发规范

- 使用中文
- 使用 uv 管理 Python 环境（`uv sync --all-packages --dev`）
- Ruff lint + format（`target-version = "py312"`, `line-length = 100`）
- 数据库迁移用 dbmate（`dbmate -d ./migrations up`）
- 提交前确保 `make test` 和 `uv run ruff check .` 通过

## MiniStack 已知限制

- Lambda Container Image：`ImageConfig.Command` 不传给 Docker CMD → 用 Zip 部署
- ECS RunTask.sync：返回成功但不真正执行容器 → Worker 用 `docker compose run`
- SFN `ListStateMachineVersions` 不支持 → `sfn.tf` 本地用 `terraform_data` + 脚本创建
- Lambda 依赖靠自定义 MiniStack 镜像预装（Warm Workers 机制）
- `LANGSMITH_TRACING=true` 会导致 Warm Worker 重复 root trace → 本地设 `false`，由 `parent_trace_from` 按需启用

## 环境配置

- `.env` 基于 `.env.example`（AWS_ENDPOINT_URL、DATABASE_URL、LANGSMITH_API_KEY 等）
- Makefile 通过 `include .env` + `export` 自动加载
- `make setup` 会将 `LANGSMITH_API_KEY` 通过 `-var` 传给 Terraform
- e2e 测试需要 Docker 运行（MiniStack + PostgreSQL + Worker 容器）

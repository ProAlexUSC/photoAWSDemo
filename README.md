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
cp .env.example .env          # 配置环境变量（可选填 LANGSMITH_API_KEY）

make setup                    # Docker Compose + migrate + build images + tofu apply
make test                     # pytest（19 个单元测试）
make test-e2e                 # 6 步端到端测试（需先 make setup）
make destroy                  # tofu destroy + docker compose down -v
```

### Makefile 完整命令

| 命令 | 说明 |
|------|------|
| `make up` | 启动 Docker Compose（MiniStack + PostgreSQL） |
| `make down` | 停止 Docker Compose |
| `make migrate` | 执行 dbmate 数据库迁移 |
| `make build-all` | 构建全部 6 个 Docker 镜像 |
| `make setup` | `up` + `migrate` + `build-all` + `tofu apply`（一键启动） |
| `make destroy` | `tofu destroy` + `docker compose down -v`（一键销毁） |
| `make test` | 运行 19 个单元测试 |
| `make test-e2e` | 运行 6 步端到端测试（依赖 `setup`） |

### 前置依赖

- Python 3.12 + [uv](https://docs.astral.sh/uv/)
- Docker
- [dbmate](https://github.com/amacneil/dbmate)
- [OpenTofu](https://opentofu.org/) (或 Terraform)

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
| Lambda 业务 | LangSmith `@traceable` | 分布式 trace 串联全 Pipeline |
| AWS 基础 | X-Ray `TracingConfig: Active` | Lambda 冷启动/SDK 调用（零代码） |

LangSmith 配置（可选）：在 `.env` 中设置 `LANGSMITH_API_KEY`，`make setup` 自动注入到 Lambda 环境变量。Scheduler 创建 parent trace，下游 Lambda 通过 event payload 传播 trace context，全 Pipeline 串联为一个 trace。

## 项目结构

```
packages/common/          共享代码（DB、BatchManager、Config、Tracing）
services/
├── scheduler/            Lambda：创建 batch，启动状态机（parent trace）
├── worker/               ECS/Batch：InsightFace 人脸检测 + 512 维 embedding
├── get_photo_ids/        Lambda：查询 photo_ids 供 Map 使用
├── tagger/               Lambda：照片打标（Stage 2，mock，@traceable）
├── vlm_extractor/        Lambda：VLM 结构化提取（Stage 3，mock，@traceable）
└── mark_complete/        Lambda：标记 batch 完成
terraform/                IaC 配置（local.tfvars / aws.tfvars 切换环境）
state-machines/           Step Functions 状态机定义（local + AWS）
migrations/               dbmate SQL 迁移
```

## 技术栈

Python 3.12 · uv workspace · OpenTofu · MiniStack · pgvector · InsightFace · Step Functions · LangSmith

## CI

GitHub Actions：push/PR → Ruff lint + format → dbmate migrate → pytest（pgvector service container）

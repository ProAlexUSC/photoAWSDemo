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
# 一键启动（Docker Compose + migrate + build images + tofu apply）
make setup

# 运行单元测试（19 tests）
make test

# 运行端到端测试（完整 Pipeline）
make test-e2e

# 销毁所有资源
make destroy
```

### 前置依赖

- Python 3.12 + [uv](https://docs.astral.sh/uv/)
- Docker
- [dbmate](https://github.com/amacneil/dbmate)
- [OpenTofu](https://opentofu.org/) (或 Terraform)

## 项目结构

```
packages/common/          共享代码（DB、BatchManager、Config）
services/
├── scheduler/            Lambda：创建 batch，启动状态机
├── worker/               ECS/Batch：InsightFace 人脸检测 + 512 维 embedding
├── get_photo_ids/        Lambda：查询 photo_ids 供 Map 使用
├── tagger/               Lambda：照片打标（Stage 2，mock）
├── vlm_extractor/        Lambda：VLM 结构化提取（Stage 3，mock）
└── mark_complete/        Lambda：标记 batch 完成
state-machines/           Step Functions 状态机定义（local + AWS）
terraform/                IaC 配置（local.tfvars / aws.tfvars 切换环境）
migrations/               dbmate SQL 迁移
```

## 技术栈

Python 3.12 · uv workspace · OpenTofu · MiniStack · pgvector · InsightFace · Step Functions · LangSmith

## CI

GitHub Actions：push/PR → Ruff lint + format → dbmate migrate → pytest（pgvector service container）

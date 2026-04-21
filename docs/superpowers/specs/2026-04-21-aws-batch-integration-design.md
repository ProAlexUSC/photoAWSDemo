# AWS Batch 集成设计（Finding #2 修复）

- 日期：2026-04-21
- 关联：Codex adversarial review Finding #2 — AWS 路径无法部署
- 作用域：`terraform/` + `state-machines/pipeline-aws.json`
- 目标：让 `tofu apply -var-file=aws.tfvars` 能从 repo 一键部署完整 AWS Pipeline

## 背景 / 原缺陷

1. `state-machines/pipeline-aws.json` 里 `JobQueue: "${GPU_JOB_QUEUE}"` 和 `JobDefinition: "${JOB_DEFINITION}"` 是字面量占位符。
2. `terraform/sfn.tf` 里 AWS 分支用 `file()` 直接读 JSON 传给 SFN，占位符不会被替换。
3. `terraform/` 里根本没有 Batch Compute Environment / Job Queue / Job Definition / 相关 IAM 资源定义。
4. 结果：`tofu apply -var-file=aws.tfvars` 会创建一个 SFN，但 state machine 定义里是字面字符串 `${GPU_JOB_QUEUE}`，执行必失败；就算不失败也没有 Batch 资源可用。

## 修复

### 新增 `terraform/batch.tf`

只在 `environment == "aws"` 时创建（`count = local.is_local ? 0 : 1`）：

- `aws_batch_compute_environment.gpu` — MANAGED EC2，`min_vcpus=0` 空闲缩 0，`max_vcpus=16`，`g4dn.xlarge`
- `aws_batch_job_queue.gpu` — 单队列，priority 1
- `aws_batch_job_definition.worker` — `var.worker_image` + 4 vCPU + 16 GB + 1 GPU
- IAM 三角：
  - `batch_service` — `batch.amazonaws.com` + `AWSBatchServiceRole` managed
  - `batch_ec2` + `batch_ec2` instance profile — `ec2.amazonaws.com` + `AmazonEC2ContainerServiceforEC2Role`
  - `batch_job` — `ecs-tasks.amazonaws.com` + inline policy（S3 read `photo-uploads`、CloudWatch Logs 写）
- 复用 default VPC / Subnets / default SG（不自建 VPC，降低部署复杂度）
- 给现有 `aws_iam_role.sfn` 追加 `aws_iam_role_policy.sfn_batch`：`batch:SubmitJob/DescribeJobs/TerminateJob` + `events:PutTargets/PutRule/DescribeRule`（`submitJob.sync` 需要）+ `lambda:InvokeFunction`

### 改 `terraform/sfn.tf`

AWS 分支从 `file()` 换成 `templatefile()`，注入两个变量：

```hcl
definition = templatefile("${path.module}/../state-machines/${var.sfn_definition_file}", {
  GPU_JOB_QUEUE  = aws_batch_job_queue.gpu[0].arn
  JOB_DEFINITION = aws_batch_job_definition.worker[0].arn
})
```

本地分支（`terraform_data "sfn_local"` + `create_sfn.sh`）完全不动。

### 改 `terraform/variables.tf`

追加三个 Batch 变量：`batch_min_vcpus` / `batch_max_vcpus` / `batch_instance_types`，都有默认值。

### 改 `terraform/aws.tfvars`

追加上述三个变量的默认值（用户可覆盖）。

### `state-machines/pipeline-aws.json` — 不改

保持 `${GPU_JOB_QUEUE}` / `${JOB_DEFINITION}` 字面量占位符。文件里其他 `$.xxx` 和 `$$.Execution.xxx` 是 ASL JSONPath，不是 templatefile 插值；templatefile 只处理 `${...}` 和 `%{...}`，对裸 `$` / `$$` 不做转义（已实测验证）。

### `Makefile` 追加（可选）

`build-push-worker-ecr` target：自动 ECR login → docker build → push。

## 部署步骤（用户视角）

### 前置

1. 配好 AWS CLI credentials（`aws configure` 或 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` 环境变量），确保 `aws sts get-caller-identity` 可用。
2. 有 default VPC（大多数 AWS 账号都有；如果删掉了需要先 `aws ec2 create-default-vpc`）。
3. 准备好 `worker_image` 的 ECR repo：
   ```bash
   aws ecr create-repository --repository-name photo-worker --region us-east-1
   ```
4. 准备 Postgres — 推荐 Supabase（Transaction mode）或 RDS；拿到 `DATABASE_URL`，已跑过 `migrations/` 里的 dbmate schema。

### Build & Push Worker Image

```bash
make build-push-worker-ecr  # 会 build + push 到 <account>.dkr.ecr.us-east-1.amazonaws.com/photo-worker:latest
```

（或手动 `docker build` + `docker push`。）

### 填 `aws.tfvars`

编辑 `terraform/aws.tfvars`：

- `lambda_database_url` — 换成你 Supabase / RDS 的真实 DSN
- `worker_image` — 换成 `<account-id>.dkr.ecr.us-east-1.amazonaws.com/photo-worker:latest`
- `batch_max_vcpus` / `batch_instance_types` — 按预算调

### Apply

```bash
cd terraform
tofu init
tofu apply -var-file=aws.tfvars \
  -var="langfuse_public_key=pk-lf-xxx" \
  -var="langfuse_secret_key=sk-lf-xxx" \
  -var="langfuse_host=https://us.cloud.langfuse.com"
```

### 验证

```bash
STATE_MACHINE_ARN=$(tofu output -raw state_machine_arn)
aws stepfunctions start-execution \
  --state-machine-arn "$STATE_MACHINE_ARN" \
  --input '{"batch_id":"test-1","s3_keys":["sample.jpg"],"langfuse_trace_id":"","langfuse_parent_observation_id":""}'
```

### 销毁

```bash
cd terraform
tofu destroy -var-file=aws.tfvars -var="langfuse_public_key=x" -var="langfuse_secret_key=x" -var="langfuse_host=x"
```

注意：Compute Environment + Job Queue 会删，CloudWatch Logs 默认 retain（AWS 行为，Batch 不是 Log Group 的 owner）。如需彻底清 logs 需手动 `aws logs delete-log-group`。

## 选型 / 约束

**为什么 Batch + GPU EC2 而不是 ECS / SageMaker / Fargate？**

- ECS on EC2：可行但自己管 ASG、capacity providers 更繁琐；Batch 是 AWS 官方 "批处理优化" 抽象，直接给我们 queue / job def / 自动 scale-to-zero。
- ECS Fargate：**不支持 GPU**。直接出局。
- SageMaker Processing：专门为 ML 训练/批推理设计，但 Step Functions 集成不如 Batch 原生（`batch:submitJob.sync`），且 SageMaker 容器规范更严格（需要 sagemaker-containers SDK 或 sagemaker-training-toolkit 的结构约定），迁移成本高。
- Lambda with GPU：不存在。
- **AWS Batch on EC2 GPU**：`min_vcpus=0` 空闲不花钱、SFN `batch:submitJob.sync` 原生支持等待完成、Job Definition 声明式、IAM 模型清晰 → 当前最佳。

**为什么不自建 VPC？**

- 大多数 AWS 账号都有 default VPC，够用。
- 自建 VPC 意味着 subnet / route table / IGW / NAT GW 一整套，对 demo 项目过度设计。
- 用户如果有现成 VPC，可以后续改 `data "aws_vpc" "default"` 为参数化 `var.vpc_id`。

## 用户需手动处理的前置

1. **ECR repo**：`aws ecr create-repository --repository-name photo-worker`（Terraform 未管理 ECR，故意的 — 避免 destroy 连带删 repo 里的 image）。
2. **Worker image push**：必须在 `tofu apply` 前 push 镜像，否则 Batch Job Definition 指向一个不存在的 image。
3. **DATABASE_URL**：Supabase / RDS 凭证需用户自己拿。如果用 RDS，要把 RDS SG 开给 Batch CE 所在的 default SG，或干脆把 Postgres 开到 Supabase。
4. **Service quota**：`g4dn.xlarge` 默认 quota 可能是 0，需要到 AWS Service Quotas 控制台申请 `Running On-Demand G and VT instances`（按 vCPU 配额，`g4dn.xlarge` = 4 vCPU）。
5. **GPU AMI**：Batch CE 的 `type=EC2` + GPU 机型会自动挑 ECS GPU-optimized AMI，一般不用额外配。

## 验证结果

- `tofu validate`：通过
- `tofu plan -var-file=aws.tfvars`：显示创建 ~20 个 Batch + IAM + data sources + 现有 Lambda / SFN / S3 等
- `tofu plan -var-file=local.tfvars`：**不**涉及 Batch 资源（count=0），本地路径无回归

（具体数量见 PR 描述。）

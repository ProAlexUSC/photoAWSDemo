-include .env
export

.PHONY: check-deps deps download-models up down migrate setup destroy test test-e2e build-worker build-all build-push-worker-ecr setup-aws apply-aws destroy-aws

BUFFALO_URL ?= https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip
MODELS_DIR := docker/models/buffalo_l

REQUIRED_BINS := docker uv dbmate tofu

check-deps:
	@missing=""; for b in $(REQUIRED_BINS); do \
		command -v $$b >/dev/null 2>&1 || missing="$$missing $$b"; \
	done; \
	if [ -n "$$missing" ]; then \
		echo "❌ 缺少依赖:$$missing"; \
		echo "   macOS: brew install$$missing  (docker 请装 OrbStack 或 Docker Desktop)"; \
		echo "   或运行: make deps"; \
		exit 1; \
	fi
	@docker compose version >/dev/null 2>&1 || { echo "❌ docker compose v2 plugin 未装"; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "❌ docker 守护进程未运行"; exit 1; }
	@[ -f .env ] || { echo "⚠️  .env 不存在，从 .env.example 复制"; cp .env.example .env; }
	@echo "✅ 依赖就绪"

deps:
	brew install dbmate opentofu uv
	@echo "👉 docker 请自行装 OrbStack 或 Docker Desktop"

up:
	docker compose up -d
	@sleep 3
	@echo "MiniStack: http://localhost:4566"
	@echo "PostgreSQL: localhost:5433"

down:
	docker compose down

migrate:
	@# postgres 启动有时需要 ~3s 初始化，dbmate 直接跑会 connection refused。轮询到可达再跑。
	@i=0; until dbmate -d ./migrations up 2>/dev/null; do \
		i=$$((i+1)); [ $$i -ge 15 ] && { echo "❌ postgres 15×2s 内没就绪；检查 docker compose ps"; exit 1; }; \
		echo "⏳ postgres 未就绪 ($$i/15)，2s 后重试"; sleep 2; \
	done
	@echo "✅ migrations applied"

download-models:
	@# 只保留 det_10g.onnx (人脸检测) + w600k_r50.onnx (embedding) —— Worker 里 allowed_modules
	@# 限定为 detection + recognition，剩下 1k3d68 / 2d106det / genderage 三个 onnx 我们不用。
	@# 去掉后镜像层从 ~275MB 降到 ~190MB，FaceAnalysis 初始化时间减少 ~40%。
	@if [ -f "$(MODELS_DIR)/det_10g.onnx" ] && [ -f "$(MODELS_DIR)/w600k_r50.onnx" ] && [ ! -f "$(MODELS_DIR)/1k3d68.onnx" ]; then \
		echo "✅ buffalo_l 已下载并精简在 $(MODELS_DIR)"; exit 0; \
	fi; \
	echo "📥 下载 InsightFace buffalo_l (~275MB) 到 $(MODELS_DIR) ..."; \
	mkdir -p "$(MODELS_DIR)"; \
	i=1; while [ $$i -le 8 ]; do \
		curl -fSL -C - --retry 3 --retry-delay 5 \
			-o /tmp/buffalo_l.zip "$(BUFFALO_URL)" && break; \
		echo "⚠️  download attempt $$i/8 failed, resuming in 5s..."; \
		i=$$((i+1)); sleep 5; \
	done; \
	[ -f /tmp/buffalo_l.zip ] || { echo "❌ 下载失败"; exit 1; }; \
	unzip -q /tmp/buffalo_l.zip -d "$(MODELS_DIR)"; \
	rm /tmp/buffalo_l.zip; \
	rm -f "$(MODELS_DIR)"/1k3d68.onnx "$(MODELS_DIR)"/2d106det.onnx "$(MODELS_DIR)"/genderage.onnx; \
	echo "✅ buffalo_l 下载 + 精简完成（仅保留 det_10g + w600k_r50）"

# Lambda 全部用 Zip 部署（terraform build_lambda_zip.sh 打包），不需要 docker image。
# Worker 才真需要 image（本地 docker-compose + AWS Batch/ECR）。
build-worker: download-models
	docker build -f services/worker/Dockerfile -t photo-worker .

build-all: build-worker

setup: check-deps up migrate build-all
	@# -replace=terraform_data.sfn_local[0]：MiniStack 重启会丢 SFN，但 filemd5 不变 tofu 不会 replay；
	@# 每次 setup 强制重建 SFN（~1s）换来幂等，否则 re-run 会撞 "StateMachineDoesNotExist"
	cd terraform && \
		([ -d .terraform ] || tofu init -input=false) && \
		(tofu workspace select default 2>/dev/null || tofu workspace new default) && \
		tofu apply -var-file=local.tfvars \
			"-replace=terraform_data.sfn_local[0]" \
			-var="langfuse_public_key=$(LANGFUSE_PUBLIC_KEY)" \
			-var="langfuse_secret_key=$(LANGFUSE_SECRET_KEY)" \
			-var="langfuse_host=$(LANGFUSE_HOST)" \
			-auto-approve

destroy:
	cd terraform && \
		(tofu workspace select default 2>/dev/null || true) && \
		tofu destroy -var-file=local.tfvars -auto-approve
	docker compose down -v

test:
	uv run pytest tests/ -v

test-e2e: setup
	uv run python scripts/test_e2e.py

##############################################
# AWS: build + push Worker image to ECR
##############################################
# 用法：AWS_REGION=us-east-1 make build-push-worker-ecr
# 前置：aws CLI 已配好 credentials；ECR repo "photo-worker" 已存在
#   aws ecr create-repository --repository-name photo-worker --region $(AWS_REGION)

AWS_REGION ?= us-east-1
ECR_REPO ?= photo-worker
AWS_ACCOUNT_ID ?= $(shell aws sts get-caller-identity --query Account --output text 2>/dev/null)

build-push-worker-ecr: download-models
	@env -u AWS_ENDPOINT_URL -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_DEFAULT_REGION \
		bash -c '\
		ACCOUNT_ID=$$(aws sts get-caller-identity --query Account --output text); \
		[ -n "$$ACCOUNT_ID" ] || { echo "❌ 无法获取 AWS account id；请配好 aws CLI"; exit 1; }; \
		aws ecr get-login-password --region $(AWS_REGION) \
			| docker login --username AWS --password-stdin $$ACCOUNT_ID.dkr.ecr.$(AWS_REGION).amazonaws.com; \
		docker buildx build --platform linux/amd64 \
			-f services/worker/Dockerfile \
			-t $$ACCOUNT_ID.dkr.ecr.$(AWS_REGION).amazonaws.com/$(ECR_REPO):latest \
			--push \
			. && \
		echo "✅ Pushed (linux/amd64): $$ACCOUNT_ID.dkr.ecr.$(AWS_REGION).amazonaws.com/$(ECR_REPO):latest"'
	# buildx --platform linux/amd64: Apple Silicon 推出的镜像在 EC2 (amd64) 能拉；
	# 否则 ECR manifest 只有 arm64，EC2 Batch 任务会 `CannotPullContainerError: no matching manifest for linux/amd64`
	# env -u 清 .env 的 MiniStack 污染（AWS_ENDPOINT_URL/test keys），避免 ecr login 400

##############################################
# AWS: apply / destroy 真 AWS 基础设施
##############################################
# 前置：.env 里设好 SUPABASE_PROJECT_REF / SUPABASE_DB_PASSWORD / LANGFUSE_*；
#      aws CLI 登录（aws login 或 aws configure），真凭证在 ~/.aws/credentials；
#      本地不要污染 AWS_* / AWS_ENDPOINT_URL（env -u 清干净）

_AWS_APPLY_CHECK := \
	[ -n "$(SUPABASE_PROJECT_REF)" ] && [ -n "$(SUPABASE_DB_PASSWORD)" ] || { \
		echo "❌ .env 里需设 SUPABASE_PROJECT_REF + SUPABASE_DB_PASSWORD"; exit 1; }

_DB_URL = postgresql://postgres.$(SUPABASE_PROJECT_REF):$(SUPABASE_DB_PASSWORD)@aws-1-$(AWS_REGION).pooler.supabase.com:6543/postgres?sslmode=require

# USE_GPU=0 切 CPU（绕 GPU 配额审批）；默认 1 用 g4dn.xlarge
# 走 -var-file 而不是 -var，避开 bash -c 单引号嵌套 list 时的 shell quoting 坑
USE_GPU ?= 1
ifeq ($(USE_GPU),0)
  _BATCH_VARS = -var-file=aws-cpu.tfvars
else
  _BATCH_VARS =
endif

# 一键上 AWS：push image + apply 基础设施
# 首次用：AWS_REGION=us-west-1 USE_GPU=0 make setup-aws
setup-aws: build-push-worker-ecr apply-aws

apply-aws:
	@$(_AWS_APPLY_CHECK)
	@env -u AWS_ENDPOINT_URL -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_DEFAULT_REGION \
		bash -c 'cd terraform && \
			([ -d .terraform ] || tofu init -input=false) && \
			(tofu workspace select aws 2>/dev/null || tofu workspace new aws) && \
			tofu apply -var-file=aws.tfvars \
				-var="lambda_database_url=$(_DB_URL)" \
				-var="langfuse_public_key=$(LANGFUSE_PUBLIC_KEY)" \
				-var="langfuse_secret_key=$(LANGFUSE_SECRET_KEY)" \
				-var="langfuse_host=$(LANGFUSE_HOST)" \
				$(_BATCH_VARS) \
				-auto-approve'

destroy-aws:
	@$(_AWS_APPLY_CHECK)
	@env -u AWS_ENDPOINT_URL -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_DEFAULT_REGION \
		bash -c 'cd terraform && \
			(tofu workspace select aws 2>/dev/null || { echo "❌ aws workspace 不存在，无需 destroy"; exit 0; }) && \
			tofu destroy -var-file=aws.tfvars \
				-var="lambda_database_url=$(_DB_URL)" \
				-var="langfuse_public_key=$(LANGFUSE_PUBLIC_KEY)" \
				-var="langfuse_secret_key=$(LANGFUSE_SECRET_KEY)" \
				-var="langfuse_host=$(LANGFUSE_HOST)" \
				$(_BATCH_VARS) \
				-auto-approve'

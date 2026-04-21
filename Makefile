-include .env
export

.PHONY: check-deps deps download-models up down migrate setup destroy test test-e2e build-worker build-scheduler build-get-photo-ids build-tagger build-vlm build-mark-complete build-all build-push-worker-ecr apply-aws destroy-aws

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
	dbmate -d ./migrations up

build-scheduler:
	docker build -f services/scheduler/Dockerfile -t photo-scheduler .

download-models:
	@if [ -f "$(MODELS_DIR)/det_10g.onnx" ]; then \
		echo "✅ buffalo_l 已下载在 $(MODELS_DIR)"; exit 0; \
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
	echo "✅ buffalo_l 下载完成"

build-worker: download-models
	docker build -f services/worker/Dockerfile -t photo-worker .

build-get-photo-ids:
	docker build -f services/get_photo_ids/Dockerfile -t get-photo-ids .

build-tagger:
	docker build -f services/tagger/Dockerfile -t photo-tagger .

build-vlm:
	docker build -f services/vlm_extractor/Dockerfile -t photo-vlm .

build-mark-complete:
	docker build -f services/mark_complete/Dockerfile -t photo-mark-complete .

build-all: build-scheduler build-worker build-get-photo-ids build-tagger build-vlm build-mark-complete

setup: check-deps up migrate build-all
	cd terraform && [ -d .terraform ] || tofu init -input=false; tofu apply -var-file=local.tfvars \
		-var="langfuse_public_key=$(LANGFUSE_PUBLIC_KEY)" \
		-var="langfuse_secret_key=$(LANGFUSE_SECRET_KEY)" \
		-var="langfuse_host=$(LANGFUSE_HOST)" \
		-auto-approve

destroy:
	cd terraform && tofu destroy -var-file=local.tfvars -auto-approve
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
	@[ -n "$(AWS_ACCOUNT_ID)" ] || { echo "❌ 无法获取 AWS account id；请配好 aws CLI"; exit 1; }
	aws ecr get-login-password --region $(AWS_REGION) \
		| docker login --username AWS --password-stdin $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com
	docker build -f services/worker/Dockerfile -t $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com/$(ECR_REPO):latest .
	docker push $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com/$(ECR_REPO):latest
	@echo "✅ Pushed: $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com/$(ECR_REPO):latest"

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
USE_GPU ?= 1
ifeq ($(USE_GPU),0)
  _BATCH_VARS = -var="batch_use_gpu=false" -var='batch_instance_types=["c5.xlarge"]'
else
  _BATCH_VARS =
endif

apply-aws:
	@$(_AWS_APPLY_CHECK)
	@env -u AWS_ENDPOINT_URL -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_DEFAULT_REGION \
		bash -c 'cd terraform && tofu workspace select aws 2>/dev/null || tofu workspace new aws; \
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
		bash -c 'cd terraform && tofu workspace select aws && \
			tofu destroy -var-file=aws.tfvars \
			-var="lambda_database_url=$(_DB_URL)" \
			-var="langfuse_public_key=$(LANGFUSE_PUBLIC_KEY)" \
			-var="langfuse_secret_key=$(LANGFUSE_SECRET_KEY)" \
			-var="langfuse_host=$(LANGFUSE_HOST)" \
			$(_BATCH_VARS) \
			-auto-approve'

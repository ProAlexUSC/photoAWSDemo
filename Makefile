include .env
export

.PHONY: up down migrate setup destroy test test-e2e build-worker build-scheduler build-get-photo-ids build-tagger build-vlm build-mark-complete build-all

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

build-worker:
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

setup: up migrate build-all
	cd terraform && [ -d .terraform ] || tofu init -input=false; tofu apply -var-file=local.tfvars -auto-approve

destroy:
	cd terraform && tofu destroy -var-file=local.tfvars -auto-approve
	docker compose down -v

test:
	uv run pytest tests/ -v

test-e2e: setup
	uv run python scripts/test_e2e.py

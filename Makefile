.PHONY: up down migrate setup test test-e2e build-worker build-scheduler

up:
	docker compose up -d
	@sleep 3
	@echo "MiniStack: http://localhost:4566"
	@echo "PostgreSQL: localhost:5433"

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

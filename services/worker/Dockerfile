FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/worker/ services/worker/

RUN uv sync --package worker --no-install-workspace --no-dev --frozen
RUN uv sync --package worker --no-dev --frozen

RUN uv run python -c "from insightface.app import FaceAnalysis; FaceAnalysis(name='buffalo_l')"

CMD ["uv", "run", "--package", "worker", "python", "-m", "worker.main"]

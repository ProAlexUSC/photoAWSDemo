FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    g++ python3-dev curl unzip \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/worker/ services/worker/

RUN uv sync --package worker --no-install-workspace --no-dev --frozen
RUN uv sync --package worker --no-dev --frozen

# 下载 InsightFace buffalo_l 模型（curl 重试比 Python 更可靠）
RUN mkdir -p /root/.insightface/models/buffalo_l && \
    curl -fSL --retry 5 --retry-delay 5 \
      -o /tmp/buffalo_l.zip \
      https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip && \
    unzip -q /tmp/buffalo_l.zip -d /root/.insightface/models/buffalo_l/ && \
    rm /tmp/buffalo_l.zip

CMD ["uv", "run", "--package", "worker", "python", "-m", "worker.main"]

FROM python:3.12-slim

# 国内镜像加速 apt-get（trixie deb822 sources + 旧格式都兼容）
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/*.sources 2>/dev/null \
 || sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null \
 || true

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

# InsightFace buffalo_l 模型：主机预下载到 docker/models/buffalo_l/，COPY 进镜像
# 好处：rebuild 秒复用；不用每次从 GitHub 拉 300MB；不依赖代理稳定性
# 由 `make download-models` 管理（build-worker 前自动触发）
COPY docker/models/buffalo_l/ /root/.insightface/models/buffalo_l/

CMD ["uv", "run", "--package", "worker", "python", "-m", "worker.main"]

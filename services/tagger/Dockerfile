FROM public.ecr.aws/lambda/python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/tagger/ services/tagger/

RUN uv sync --package tagger --no-install-workspace --no-dev --frozen
RUN uv sync --package tagger --no-dev --frozen

FROM public.ecr.aws/lambda/python:3.12

COPY --from=builder /app/.venv/lib/python3.12/site-packages/ ${LAMBDA_TASK_ROOT}/

COPY services/tagger/src/tagger/ ${LAMBDA_TASK_ROOT}/tagger/
COPY packages/common/src/common/ ${LAMBDA_TASK_ROOT}/common/

CMD ["tagger.handler.handler"]

FROM public.ecr.aws/lambda/python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/scheduler/ services/scheduler/

RUN uv sync --package scheduler --no-emit-workspace --no-dev --frozen
RUN uv sync --package scheduler --no-dev --frozen

FROM public.ecr.aws/lambda/python:3.12

COPY --from=builder /app/.venv/lib/python3.12/site-packages/ ${LAMBDA_TASK_ROOT}/

COPY services/scheduler/src/scheduler/ ${LAMBDA_TASK_ROOT}/scheduler/
COPY packages/common/src/common/ ${LAMBDA_TASK_ROOT}/common/

CMD ["scheduler.handler.handler"]

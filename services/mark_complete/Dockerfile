FROM public.ecr.aws/lambda/python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/mark_complete/ services/mark_complete/

RUN uv sync --package mark-complete --no-install-workspace --no-dev --frozen
RUN uv sync --package mark-complete --no-dev --frozen

FROM public.ecr.aws/lambda/python:3.12

COPY --from=builder /app/.venv/lib/python3.12/site-packages/ ${LAMBDA_TASK_ROOT}/

COPY services/mark_complete/src/mark_complete/ ${LAMBDA_TASK_ROOT}/mark_complete/
COPY packages/common/src/common/ ${LAMBDA_TASK_ROOT}/common/

CMD ["mark_complete.handler.handler"]

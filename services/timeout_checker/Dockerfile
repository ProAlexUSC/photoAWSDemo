FROM public.ecr.aws/lambda/python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/timeout_checker/ services/timeout_checker/

RUN uv sync --package timeout-checker --no-install-workspace --no-dev --frozen
RUN uv sync --package timeout-checker --no-dev --frozen

FROM public.ecr.aws/lambda/python:3.12
COPY --from=builder /app/.venv/lib/python3.12/site-packages/ ${LAMBDA_TASK_ROOT}/
COPY services/timeout_checker/src/timeout_checker/ ${LAMBDA_TASK_ROOT}/timeout_checker/
COPY packages/common/src/common/ ${LAMBDA_TASK_ROOT}/common/

CMD ["timeout_checker.handler.handler"]

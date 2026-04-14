FROM public.ecr.aws/lambda/python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/vlm_extractor/ services/vlm_extractor/

RUN uv sync --package vlm-extractor --no-install-workspace --no-dev --frozen
RUN uv sync --package vlm-extractor --no-dev --frozen

FROM public.ecr.aws/lambda/python:3.12

COPY --from=builder /app/.venv/lib/python3.12/site-packages/ ${LAMBDA_TASK_ROOT}/

COPY services/vlm_extractor/src/vlm_extractor/ ${LAMBDA_TASK_ROOT}/vlm_extractor/
COPY packages/common/src/common/ ${LAMBDA_TASK_ROOT}/common/

CMD ["vlm_extractor.handler.handler"]

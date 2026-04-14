FROM public.ecr.aws/lambda/python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/get_photo_ids/ services/get_photo_ids/

RUN uv sync --package get-photo-ids --no-install-workspace --no-dev --frozen
RUN uv sync --package get-photo-ids --no-dev --frozen

FROM public.ecr.aws/lambda/python:3.12

COPY --from=builder /app/.venv/lib/python3.12/site-packages/ ${LAMBDA_TASK_ROOT}/

COPY services/get_photo_ids/src/get_photo_ids/ ${LAMBDA_TASK_ROOT}/get_photo_ids/
COPY packages/common/src/common/ ${LAMBDA_TASK_ROOT}/common/

CMD ["get_photo_ids.handler.handler"]

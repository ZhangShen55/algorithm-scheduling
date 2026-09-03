FROM python:3.11-slim AS builder

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple

ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10

WORKDIR /build

COPY online_gateway_service/requirements.txt /tmp/requirements.txt
COPY algorithm-scheduling-platform/pyproject.toml /tmp/platform/pyproject.toml
COPY algorithm-scheduling-platform/packages /tmp/platform/packages
RUN --mount=type=cache,id=algorithm-platform-pip-cache,target=/root/.cache/pip,sharing=locked \
    python -m pip wheel \
        --wheel-dir /wheelhouse \
        --timeout 300 \
        --retries 10 \
        --prefer-binary \
        -r /tmp/requirements.txt \
        /tmp/platform

FROM python:3.11-slim

ARG EXPECTED_GIT_SHA
LABEL org.opencontainers.image.revision="${EXPECTED_GIT_SHA}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN mkdir -p /app/logs

RUN --mount=type=bind,from=builder,source=/wheelhouse,target=/tmp/wheelhouse \
    --mount=type=bind,from=builder,source=/tmp/requirements.txt,target=/tmp/requirements.txt \
    python -m pip install \
        --no-cache-dir \
        --no-index \
        --find-links=/tmp/wheelhouse \
        -r /tmp/requirements.txt \
        algorithm-scheduling-platform

COPY online_gateway_service/app /app/app
COPY algorithm-scheduling-platform/packages /app/packages
COPY online_gateway_service/config.toml /app/config.toml
COPY algorithm-scheduling-platform/deploy/scripts/source_manifest.py /usr/local/bin/source-manifest
RUN python /usr/local/bin/source-manifest generate \
        --revision "${EXPECTED_GIT_SHA}" \
        --source /app/app=app \
        --source /app/packages=packages \
        --output /app/.source-manifest.json \
        > /app/.source-manifest.sha256

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]

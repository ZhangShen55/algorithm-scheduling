FROM python:3.11-slim AS builder

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple

ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10

WORKDIR /build

COPY vision_orchestrator_service/requirements.txt /tmp/requirements.txt
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

ARG DEBIAN_MIRROR=https://mirrors.aliyun.com/debian
ARG DEBIAN_SECURITY_MIRROR=https://mirrors.aliyun.com/debian-security
ARG EXPECTED_GIT_SHA
LABEL org.opencontainers.image.revision="${EXPECTED_GIT_SHA}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN mkdir -p /app/logs

RUN sed -i \
        -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
        -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get \
        -o Acquire::Retries=10 \
        -o Acquire::http::Timeout=300 \
        -o Acquire::https::Timeout=300 \
        update \
    && apt-get \
        -o Acquire::Retries=10 \
        -o Acquire::http::Timeout=300 \
        -o Acquire::https::Timeout=300 \
        install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=bind,from=builder,source=/wheelhouse,target=/tmp/wheelhouse \
    --mount=type=bind,from=builder,source=/tmp/requirements.txt,target=/tmp/requirements.txt \
    python -m pip install \
        --no-cache-dir \
        --no-index \
        --find-links=/tmp/wheelhouse \
        -r /tmp/requirements.txt \
        algorithm-scheduling-platform

COPY vision_orchestrator_service/app /app/app
COPY algorithm-scheduling-platform/packages /app/packages
COPY vision_orchestrator_service/config.toml /app/config.toml
COPY algorithm-scheduling-platform/deploy/scripts/source_manifest.py /usr/local/bin/source-manifest
RUN python /usr/local/bin/source-manifest generate \
        --revision "${EXPECTED_GIT_SHA}" \
        --source /app/app=app \
        --source /app/packages=packages \
        --output /app/.source-manifest.json \
        > /app/.source-manifest.sha256

EXPOSE 8010

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "1"]

# syntax=docker/dockerfile:1.7

FROM pytorch/pytorch:2.6.0-cuda11.8-cudnn9-runtime AS runtime-base

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ARG OPERATOR_REGISTRY_CLIENT_WHEEL=algorithm_operator_registry_client-0.2.0-py3-none-any.whl
COPY wheel/${OPERATOR_REGISTRY_CLIENT_WHEEL} /tmp/wheels/${OPERATOR_REGISTRY_CLIENT_WHEEL}
RUN --mount=type=bind,source=docker/requirements-docker.txt,target=/tmp/requirements-docker.txt,ro \
    pip install --no-deps /tmp/wheels/${OPERATOR_REGISTRY_CLIENT_WHEEL} \
    && pip install --retries 10 --timeout 120 \
      -r /tmp/requirements-docker.txt -i "$PIP_INDEX_URL" \
    && pip install --retries 10 --timeout 120 \
      "ultralytics>=8.3.120" --no-deps -i "$PIP_INDEX_URL"


FROM runtime-base AS cython-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "Cython>=3.0,<4" setuptools -i "$PIP_INDEX_URL"

WORKDIR /build
COPY app/ ./app/
COPY docker/build_cython_modules.py ./build_cython_modules.py

RUN python build_cython_modules.py \
      --root /build \
      --package app \
      --keep-source app/main.py \
      --remove-sources \
    && rm -rf build build_cython_modules.py \
    && find /build/app -type d -name '__pycache__' -prune -exec rm -rf {} + \
    && touch /tmp/screen-det-build-config.toml \
    && CONFIG_PATH=/tmp/screen-det-build-config.toml \
      python -c "from app.main import app; assert app.openapi(); print('cython app smoke ok')" \
    && rm -f /tmp/screen-det-build-config.toml


FROM runtime-base

WORKDIR /app

COPY --from=cython-builder /build/app ./app
COPY model/ ./model/
COPY docker/start.sh /usr/local/bin/start-screen-det

RUN chmod 0755 /usr/local/bin/start-screen-det \
    && mkdir -p /app/logs /dev/shm/screen-det-models

EXPOSE 8880

CMD ["/usr/local/bin/start-screen-det"]

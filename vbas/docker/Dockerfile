FROM pytorch/pytorch:2.6.0-cuda11.8-cudnn9-runtime

LABEL authors="SeaCraft"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        build-essential \
        jq \
        curl \
        libgl1-mesa-glx \
        libglib2.0-0 \
        libsm6 \
        libxrender1 \
        libxext6 && \
    rm -rf /var/lib/apt/lists/* && \
    apt-get clean

WORKDIR /workspace

COPY ./requirements.txt ./requirements.txt
ARG OPERATOR_REGISTRY_CLIENT_WHEEL=algorithm_operator_registry_client-0.2.0-py3-none-any.whl
COPY wheel/${OPERATOR_REGISTRY_CLIENT_WHEEL} /tmp/wheels/${OPERATOR_REGISTRY_CLIENT_WHEEL}
RUN pip install --no-deps /tmp/wheels/${OPERATOR_REGISTRY_CLIENT_WHEEL} && \
    pip3 install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    --timeout 300 \
    --retries 5 \
    --trusted-host mirrors.aliyun.com \
    -r requirements.txt

COPY ./app ./app
COPY ./models ./models
COPY ./docker/start.sh ./docker/start.sh
COPY ./scripts/build_cython_modules.py ./scripts/build_cython_modules.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG_PATH=/workspace/config.toml

RUN chmod +x docker/start.sh
ARG PROTECT_SOURCE=0
RUN set -e; \
    if [ "$PROTECT_SOURCE" = "1" ]; then \
        python scripts/build_cython_modules.py \
          --root /workspace \
          --package app/api \
          --package app/core \
          --package app/services \
          --package app/schemas \
          --keep-source app/__init__.py \
          --keep-source app/main.py \
          --keep-source app/core/__init__.py \
          --keep-source app/api/__init__.py \
          --keep-source app/services/__init__.py \
          --keep-source app/schemas/__init__.py \
          --exclude-glob 'app/vendor/**' \
          --exclude-glob 'models/**' \
          --remove-sources; \
        rm -rf build; \
        find /workspace -type d -name '__pycache__' -prune -exec rm -rf {} +; \
    fi

EXPOSE 8981
CMD ["/bin/bash", "-c", "docker/start.sh"]

FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04

ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG FASTDEPLOY_FIND_LINKS=https://www.paddlepaddle.org.cn/whl/fastdeploy.html

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

WORKDIR /app

RUN apt-get update && apt-get install -y \
    python3.10 \
    python3-pip \
    python3-dev \
    build-essential \
    cmake \
    libopencv-dev \
    libboost-all-dev \
    wget \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir \
    --index-url "$PYPI_INDEX_URL" \
    --timeout 300 \
    --retries 10 \
    --prefer-binary \
    --upgrade pip setuptools wheel

COPY requirements.txt /app/
ARG OPERATOR_REGISTRY_CLIENT_WHEEL=algorithm_operator_registry_client-0.2.0-py3-none-any.whl
COPY wheel/${OPERATOR_REGISTRY_CLIENT_WHEEL} /tmp/wheels/${OPERATOR_REGISTRY_CLIENT_WHEEL}
RUN python3 -m pip install --no-deps /tmp/wheels/${OPERATOR_REGISTRY_CLIENT_WHEEL} && \
    python3 -m pip install --no-cache-dir \
        --index-url "$PYPI_INDEX_URL" \
        --find-links "$FASTDEPLOY_FIND_LINKS" \
        --timeout 300 \
        --retries 10 \
        --prefer-binary \
        -r requirements.txt

# 只打包运行源码与模型；配置由 Compose 只读挂载，media 只属于运行期数据。
COPY app /app/app
COPY ai_models /app/ai_models
COPY docker/entrypoint.sh /usr/local/bin/facerec-entrypoint

RUN chmod 0755 /usr/local/bin/facerec-entrypoint && \
    mkdir -p /app/logs /app/uploaded_faces

EXPOSE 8000

CMD ["/usr/local/bin/facerec-entrypoint"]

# syntax=docker/dockerfile:1.7

########################
# 1) 构建阶段（加密）
########################
FROM python:3.11 AS builder

ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /build

# 安装运行依赖 + PyArmor
COPY requirements.txt /build/
ARG OPERATOR_REGISTRY_CLIENT_WHEEL=algorithm_operator_registry_client-0.1.0-py3-none-any.whl
COPY wheel/${OPERATOR_REGISTRY_CLIENT_WHEEL} /tmp/wheels/${OPERATOR_REGISTRY_CLIENT_WHEEL}
RUN pip install --no-deps /tmp/wheels/${OPERATOR_REGISTRY_CLIENT_WHEEL} && \
    pip install --no-cache-dir \
        --index-url "$PYPI_INDEX_URL" \
        --timeout 300 \
        --retries 10 \
        --prefer-binary \
        -r requirements.txt && \
    pip install --no-cache-dir \
        --index-url "$PYPI_INDEX_URL" \
        --timeout 300 \
        --retries 10 \
        --prefer-binary \
        pyarmor

# 拷贝源码（config.toml 不拷贝，运行时挂载）
COPY app ./app
COPY prompt ./prompt

# 使用 PyArmor 混淆 app 目录，输出到 /build/obf
# PyArmor 8.x：pyarmor gen -O <outdir> --recursive <package_or_file>
RUN pyarmor gen -O /build/obf --recursive app

# 资源文件保留原样（如 prompt）
# RUN mkdir -p /build/obf/prompt && cp -r   /* /build/obf/prompt/
# 资源文件保留原样（如 prompt）
RUN mkdir -p /build/obf/prompt && \
    if [ -d "prompt" ]; then cp -a prompt/. /build/obf/prompt/; fi

# start.sh 放进产物里（运行阶段会复制）
COPY start.sh /build/obf/start.sh
RUN chmod +x /build/obf/start.sh

########################
# 2) 运行阶段（仅加密产物）
########################
FROM python:3.11 AS runtime

ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UVICORN_HOST=0.0.0.0 \
    UVICORN_PORT=8000 \
    UVICORN_WORKERS=1 \
    UVICORN_RELOAD=0 \
    UVICORN_LOG_LEVEL=info \
    APP_MODULE=app.main:app

WORKDIR /app

# 仅装运行依赖
COPY requirements.txt /app/
ARG OPERATOR_REGISTRY_CLIENT_WHEEL=algorithm_operator_registry_client-0.1.0-py3-none-any.whl
COPY wheel/${OPERATOR_REGISTRY_CLIENT_WHEEL} /tmp/wheels/${OPERATOR_REGISTRY_CLIENT_WHEEL}
RUN pip install --no-deps /tmp/wheels/${OPERATOR_REGISTRY_CLIENT_WHEEL} && \
    pip install --no-cache-dir \
        --index-url "$PYPI_INDEX_URL" \
        --timeout 300 \
        --retries 10 \
        --prefer-binary \
        -r /app/requirements.txt

# 拷贝混淆后的代码与启动脚本
COPY --from=builder /build/obf /app

EXPOSE 8000

# 健康检查（可选）
# HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
#   CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/config',timeout=2).getcode()==200 else 1)"


# 不直接写 uvicorn，改用 start.sh 启动
ENTRYPOINT ["/bin/bash", "/app/start.sh"]

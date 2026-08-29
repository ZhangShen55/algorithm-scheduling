FROM python:3.11-slim

RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /workspace
COPY app /workspace/app
COPY requirements.txt /workspace/requirements.txt
COPY wheel /workspace/wheel

RUN mkdir -p /workspace/logs

ARG OPERATOR_REGISTRY_CLIENT_WHEEL=algorithm_operator_registry_client-0.2.0-py3-none-any.whl
RUN pip install --no-deps wheel/${OPERATOR_REGISTRY_CLIENT_WHEEL} \
    && pip install --no-cache-dir -r requirements.txt

ENV CONFIG_PATH=/workspace/config.toml \
    RESULT_ROOT=/data/result

VOLUME ["/data/result"]

EXPOSE 9001
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9001", "--workers", "1"]

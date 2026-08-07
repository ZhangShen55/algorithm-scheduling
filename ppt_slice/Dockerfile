FROM python:3.11-slim

RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /workspace
COPY . /workspace/

RUN pip install --no-cache-dir -r requirements.txt

ENV RESULT_ROOT=/data/result
VOLUME ["/data/result"]

EXPOSE 9001
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9001", "--workers", "1"]

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY online_gateway_service/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY algorithm-scheduling-platform/pyproject.toml /tmp/platform/pyproject.toml
COPY algorithm-scheduling-platform/packages /tmp/platform/packages
RUN pip install --no-cache-dir /tmp/platform

COPY online_gateway_service/app /app/app
COPY online_gateway_service/config.toml /app/config.toml

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY control_service/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY algorithm-scheduling-platform/pyproject.toml /tmp/platform/pyproject.toml
COPY algorithm-scheduling-platform/packages /tmp/platform/packages
RUN pip install --no-cache-dir /tmp/platform

COPY control_service/app /app/app
COPY control_service/config.toml /app/config.toml

EXPOSE 18100

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18100", "--workers", "1"]

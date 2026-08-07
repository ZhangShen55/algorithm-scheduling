FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY orchestrator_service/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY algorithm-scheduling-platform/pyproject.toml /tmp/platform/pyproject.toml
COPY algorithm-scheduling-platform/packages /tmp/platform/packages
RUN pip install --no-cache-dir /tmp/platform

COPY orchestrator_service/app /app/app
COPY orchestrator_service/config.toml /app/config.toml

EXPOSE 18101

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18101", "--workers", "1"]

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY services/orchestrator_service/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY packages /app/packages
COPY services /app/services

EXPOSE 18101

CMD ["python", "-m", "uvicorn", "services.orchestrator_service.app.main:app", "--host", "0.0.0.0", "--port", "18101", "--workers", "1"]


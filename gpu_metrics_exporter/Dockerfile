FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY gpu_metrics_exporter/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt
COPY gpu_metrics_exporter/app.py /app/app.py

EXPOSE 9400
CMD ["python", "/app/app.py"]

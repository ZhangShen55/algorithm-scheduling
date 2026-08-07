FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/control_service/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY packages /app/packages
COPY services /app/services

EXPOSE 18100

CMD ["python", "-m", "uvicorn", "services.control_service.app.main:app", "--host", "0.0.0.0", "--port", "18100", "--workers", "1"]


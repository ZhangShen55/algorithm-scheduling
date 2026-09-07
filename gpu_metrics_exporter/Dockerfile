ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY gpu_metrics_exporter/requirements.txt /tmp/requirements.txt
COPY gpu_metrics_exporter/wheels /tmp/wheels
RUN if find /tmp/wheels -maxdepth 1 -name '*.whl' -print -quit | grep -q .; then \
      python -m pip install --no-cache-dir --no-index --find-links=/tmp/wheels -r /tmp/requirements.txt; \
    else \
      python -m pip install --no-cache-dir -r /tmp/requirements.txt; \
    fi
COPY gpu_metrics_exporter/app.py /app/app.py

EXPOSE 9400
CMD ["python", "/app/app.py"]

FROM ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddle:3.3.0-gpu-cuda11.8-cudnn8.9

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    LD_LIBRARY_PATH=/usr/local/nvidia/lib64:/usr/local/nvidia/lib:/usr/local/cuda-11.8/compat:/usr/local/cuda-11.8/targets/x86_64-linux/lib

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.gpu.txt ./
RUN python -m pip install --no-cache-dir -r requirements.gpu.txt

COPY app ./app
COPY models ./models
COPY scripts/verify_models.py ./scripts/verify_models.py

RUN python scripts/verify_models.py

EXPOSE 8866

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8866", "--workers", "1"]

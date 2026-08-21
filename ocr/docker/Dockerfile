ARG BASE_IMAGE=ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddle:3.3.0-gpu-cuda11.8-cudnn8.9
ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

FROM ${BASE_IMAGE} AS app-builder

ARG cython=no
ARG PYPI_INDEX_URL

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY app /build/source/app
COPY docker/build_cython.py /tmp/build_cython.py

RUN case "$cython" in \
      yes|no) ;; \
      *) echo 'cython must be "yes" or "no"' >&2; exit 2 ;; \
    esac \
    && if [ "$cython" = "yes" ]; then \
         command -v gcc >/dev/null \
         && command -v make >/dev/null \
         && python -m pip install --no-cache-dir \
              --index-url "$PYPI_INDEX_URL" \
              Cython==3.1.3 setuptools==80.9.0; \
       fi \
    && python /tmp/build_cython.py \
         --source-app /build/source/app \
         --output-app /build/application/app \
         --mode "$cython"

FROM ${BASE_IMAGE} AS runtime

ARG PYPI_INDEX_URL
ARG OPERATOR_REGISTRY_CLIENT_WHEEL=algorithm_operator_registry_client-0.2.0-py3-none-any.whl

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/usr/local/nvidia/lib64:/usr/local/nvidia/lib:/usr/local/cuda-11.8/targets/x86_64-linux/lib

WORKDIR /app

COPY requirements.txt /tmp/requirements/
COPY wheel/${OPERATOR_REGISTRY_CLIENT_WHEEL} /tmp/wheels/${OPERATOR_REGISTRY_CLIENT_WHEEL}

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && python -m pip install --no-deps /tmp/wheels/${OPERATOR_REGISTRY_CLIENT_WHEEL} \
    && python -m pip install \
         --no-cache-dir \
         --timeout 120 \
         --retries 10 \
         --index-url "$PYPI_INDEX_URL" \
         -r /tmp/requirements/requirements.txt \
    && python -c "from importlib.metadata import version; actual = version('paddlepaddle-gpu'); assert actual == '3.3.0', f'基础镜像 PaddlePaddle GPU 版本错误：{actual}'" \
    && rm -rf /tmp/requirements /tmp/wheels \
    && apt-get purge -y --auto-remove gcc g++ make build-essential \
    && rm -rf /usr/local/ccache-* /usr/local/bin/ccache /home/cmake-* \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && ! command -v gcc

COPY --from=app-builder /build/application/app ./app
COPY models ./models
COPY scripts/verify_models.py /app/.build/verify_models.py
COPY config.toml.example /app/.build/config.toml
COPY docker/entrypoint.sh /usr/local/bin/ocr-entrypoint

RUN --mount=type=secret,id=ocr_model_manifest,required=true \
    chmod 0755 /usr/local/bin/ocr-entrypoint \
    && python /app/.build/verify_models.py \
         --models-root /app/models \
         --manifest /run/secrets/ocr_model_manifest \
         --exact \
    && install -m 0444 /run/secrets/ocr_model_manifest /app/models/manifest.sha256 \
    && cp /app/.build/config.toml /app/config.toml \
    && mkdir -p /app/logs \
    && python -c "import app.main; import app.engines.paddleocr_v6; import app.engines.paddle_formula; import app.services.ocr_service; import app.services.formula_service" \
    && rm -f /app/config.toml \
    && rm -rf /app/.build

EXPOSE 8866

CMD ["/usr/local/bin/ocr-entrypoint"]

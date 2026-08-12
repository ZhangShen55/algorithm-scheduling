# ================== 依赖层：系统 & Conda & PIP 依赖（无业务源码） ==================
FROM nvcr.io/nvidia/cuda:12.1.1-cudnn8-runtime-centos7 AS deps

RUN mv /etc/yum.repos.d/CentOS-Base.repo /etc/yum.repos.d/CentOS-Base.repo.bak && \
    curl -o /etc/yum.repos.d/CentOS-Base.repo http://mirrors.aliyun.com/repo/Centos-7.repo && \
    yum clean all && yum makecache && \
    yum -y install epel-release git wget bzip2 ca-certificates && \
    rpm --import http://li.nux.ro/download/nux/RPM-GPG-KEY-nux.ro && \
    rpm -Uvh http://li.nux.ro/download/nux/dextop/el7/x86_64/nux-dextop-release-0-5.el7.nux.noarch.rpm && \
    yum clean all && yum install -y vim

# CentOS 7 仅 glibc 2.17；latest Miniconda 安装器要求 glibc >= 2.28，须固定旧版
ARG MINICONDA_INSTALLER=Miniconda3-py311_23.11.0-2-Linux-x86_64.sh
RUN wget -q "https://repo.anaconda.com/miniconda/${MINICONDA_INSTALLER}" -O /tmp/miniconda.sh && \
    bash /tmp/miniconda.sh -b -p /opt/conda && rm -f /tmp/miniconda.sh
ENV PATH="/opt/conda/bin:$PATH"

RUN conda config --system --set show_channel_urls true && \
    conda config --system --set channel_priority strict && \
    conda config --system --remove channels defaults || true && \
    conda config --system --add channels conda-forge && \
    conda config --system --remove-key default_channels || true && \
    conda config --system --add default_channels https://conda.anaconda.org/conda-forge && \
    conda info

# av 须从 conda 安装（PyPI 在 CentOS7 上常无 wheel，源码编译需 ffmpeg pkg-config）
RUN conda create -n asr -y -c conda-forge \
    python=3.11 \
    "ffmpeg>=6,<8" \
    av=14.4.0 \
    libsndfile \
    pysoundfile \
    pkg-config \
    && conda clean -afy
SHELL ["bash", "-lc"]
ENV PATH="/opt/conda/envs/asr/bin:$PATH"
RUN python --version && ldd --version

RUN PIP_EXTRA_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
    pip install --only-binary=sentencepiece --prefer-binary sentencepiece==0.2.0
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

WORKDIR /tmp/build
ARG OPERATOR_REGISTRY_CLIENT_WHEEL=algorithm_operator_registry_client-0.1.0-py3-none-any.whl
COPY wheel/${OPERATOR_REGISTRY_CLIENT_WHEEL} /tmp/wheels/${OPERATOR_REGISTRY_CLIENT_WHEEL}
COPY requirements-pip.txt /tmp/requirements-pip.txt
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-deps /tmp/wheels/${OPERATOR_REGISTRY_CLIENT_WHEEL} && \
    pip install --only-binary=:all: pyarrow==20.0.0 && \
    pip install --no-cache-dir --retries 10 --timeout 600 \
      --prefer-binary \
      -r /tmp/requirements-pip.txt


# ================== 混淆层：只接触业务源码，输出 PyArmor 混淆产物 ==================
FROM deps AS obfuscator

WORKDIR /build
COPY app/ app/

RUN pip install --no-cache-dir pyarmor==8.5.12 && \
    pyarmor gen --recursive --output /opt/obfuscated app && \
    find /opt/obfuscated -type d -name "__pycache__" -prune -exec rm -rf {} + && \
    find /opt/obfuscated -type f -name "*.pyc" -delete


# ================== 运行层：混淆代码 + 内置模型；config.toml 运行时挂载 ==================
FROM deps AS runtime

WORKDIR /app
ENV CONFIG_PATH=/config.toml
ENV CONDA_ENV_NAME=asr
ENV MODEL_ROOT=/app/model

COPY --from=obfuscator /opt/obfuscated/ ./
COPY docker/start.sh ./
COPY model/ model/

RUN chmod +x ./start.sh && \
    mkdir -p /var/model_zoo && \
    ln -sfnT /app/model /model && \
    ln -sfnT /app/model /var/model_zoo/model_asr

EXPOSE 8083
CMD ["/bin/bash", "-c", "./start.sh"]

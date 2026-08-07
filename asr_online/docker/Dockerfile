# ================== 依赖层：系统 & Conda & PIP 依赖（无源码） ==================
FROM nvcr.io/nvidia/cuda:12.1.1-cudnn8-runtime-centos7 AS deps

# 基础工具
RUN mv /etc/yum.repos.d/CentOS-Base.repo /etc/yum.repos.d/CentOS-Base.repo.bak && \
    curl -o /etc/yum.repos.d/CentOS-Base.repo http://mirrors.aliyun.com/repo/Centos-7.repo && \
    yum clean all && yum makecache && \
    yum -y install epel-release git wget bzip2 ca-certificates vim && \
    yum clean all

# Miniconda
# CentOS 7 仅 glibc 2.17；latest Miniconda 安装器要求 glibc >= 2.28
ARG MINICONDA_INSTALLER=Miniconda3-py310_23.11.0-2-Linux-x86_64.sh
RUN wget -q "https://repo.anaconda.com/miniconda/${MINICONDA_INSTALLER}" -O /tmp/miniconda.sh && \
    bash /tmp/miniconda.sh -b -p /opt/conda && rm -f /tmp/miniconda.sh
ENV PATH="/opt/conda/bin:$PATH"

# conda-forge
RUN conda config --system --set show_channel_urls true && \
    conda config --system --set channel_priority strict && \
    conda config --system --remove channels defaults || true && \
    conda config --system --add channels conda-forge && \
    conda config --system --remove-key default_channels || true && \
    conda config --system --add default_channels https://conda.anaconda.org/conda-forge && \
    conda info

# Python 环境（在线服务不需要 libsndfile / pysoundfile）
RUN conda create -n seacraftasr_online -y -c conda-forge python=3.10 && conda clean -afy
SHELL ["bash", "-lc"]
ENV PATH="/opt/conda/envs/seacraftasr_online/bin:$PATH"
RUN python --version

# pip 源配置
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

# 安装依赖（只拷 requirements.txt，不引入源码）
COPY requirements.txt /tmp/requirements.txt
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir --retries 10 --timeout 60 --prefer-binary \
    -r /tmp/requirements.txt


# ================== 运行层 ==================
FROM deps AS runtime
WORKDIR /app

# 拷贝源码与内置模型
COPY . /app

# 配置文件
COPY config.toml /config.toml

COPY docker/start.sh /app/start.sh

# 权限
RUN chmod +x ./start.sh

# 一个容器只暴露一个可注册的 Uvicorn 端点
EXPOSE 8084

CMD ["/bin/bash", "-c", "./start.sh"]

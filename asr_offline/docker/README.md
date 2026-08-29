# ASR Offline 镜像构建

构建上下文必须是项目根目录，镜像会包含 `model/` 下的模型文件和
`wheel/algorithm_operator_registry_client-0.1.0-py3-none-any.whl`。构建前确认两者均存在。

```bash
cd /path/to/asr_offline
docker buildx version
```

## AMD64

当前交付镜像仅支持 `linux/amd64`，用于配有 NVIDIA GPU 的 x86_64 Linux 服务器。
构建并加载到本机 Docker：

```bash
docker buildx build \
  --platform linux/amd64 \
  --load \
  --tag seacraft-asr-offline:v1.0_260812 \
  --file docker/Dockerfile \
  .
```

导出 tar 包用于离线交付：

```bash
docker save --output seacraft-asr-offline-v1.0_260812.tar \
  seacraft-asr-offline:v1.0_260812
```

运行、验收和回滚请参见 [部署说明](部署说明.md)。

## ARM64

当前 Dockerfile **不支持** `linux/arm64` 的可交付镜像。其 CUDA CentOS 7 基础镜像、
Nux RPM 地址和 Miniconda 安装器都固定为 x86_64；即使以下 Buildx 命令能够开始执行，
也不能将结果作为 ARM64 发布镜像使用。

```bash
docker buildx build \
  --platform linux/arm64 \
  --tag asr_offline:v2.0_arm64 \
  --file docker/Dockerfile \
  .
```

若需要正式支持 ARM64，需要先适配 ARM64 CUDA 或 CPU 基础镜像、系统包安装方式、
Miniconda 安装器和全部 Python/模型运行时依赖，并在 ARM64 目标机完成真实转写验收后，
再将本节改为正式构建命令。

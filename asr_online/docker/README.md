# ASR Online Docker 部署

所有命令都从 `asr_online/` 项目根目录执行，Docker 构建上下文必须为项目根目录 `.`。
镜像内已经包含 `app/` 和 `model/`，运行时必须只读挂载一份 `config.toml`。

## 构建镜像

```bash
docker build \
  -f docker/Dockerfile \
  -t seacraft-asr-online:local \
  .
```

需要 Cython 运行镜像时使用：

```bash
docker build \
  -f docker/Dockerfile.cython \
  -t seacraft-asr-online:cython \
  .
```

## 准备配置

将实际配置保存为宿主机文件，例如：

```text
/opt/algorithm-operators/asr_online/config.toml
```

GPU 部署时，配置中的 `device` 应为容器内设备编号（单卡映射时为 `cuda:0`），`ngpu` 应为
`1`，并将 `[runtime].require_gpu` 设为 `true`。一个容器只运行一个 Uvicorn worker。

## 启动容器

以下命令把物理 GPU 0 映射给一个 ASR Online 实例：

```bash
docker run -d \
  --name asr-online-gpu0 \
  --restart unless-stopped \
  --gpus '"device=0"' \
  -p 8084:8084 \
  -v /opt/algorithm-operators/asr_online/config.toml:/config.toml:ro \
  -e CONFIG_PATH=/config.toml \
  -e PORT=8084 \
  -e UVICORN_WORKERS=1 \
  seacraft-asr-online:local
```

本地 CPU 配置可以删除 `--gpus` 参数，并保持 `device="cpu"`、`ngpu=0` 和
`runtime.require_gpu=false`。

接入调度平台时，还要把容器加入平台网络，并提供实例级注册信息：

```bash
docker run -d \
  --name asr-online-gpu0 \
  --restart unless-stopped \
  --network algorithm-platform \
  --gpus '"device=0"' \
  -p 127.0.0.1:18084:8084 \
  -v /opt/algorithm-operators/asr_online/config.toml:/config.toml:ro \
  -e CONFIG_PATH=/config.toml \
  -e PORT=8084 \
  -e UVICORN_WORKERS=1 \
  -e PLATFORM_INSTANCE_ID=asr-online-gpu0 \
  -e PLATFORM_SERVICE_URL=http://asr-online-gpu0:8084 \
  -e PLATFORM_GPU_ID=0 \
  -e PLATFORM_OPERATOR_REGISTRY_TOKEN=REPLACE_WITH_TOKEN \
  seacraft-asr-online:local
```

受控部署使用的 TOML 需要启用注册，并把 `platform.control_service_url` 指向同一 Docker
网络中的 Control Service。WebSocket 地址为
`ws://HOST:8084/v1.0.1/seacraft_asr_online`。

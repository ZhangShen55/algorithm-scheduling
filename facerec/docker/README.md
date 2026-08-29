# FaceRec Docker 部署

所有命令都从 `facerec/` 项目根目录执行，Docker 构建上下文必须为项目根目录 `.`。镜像内
已经包含应用和 `ai_models/`；配置、日志及可变人员图片在运行时挂载。

## 构建镜像

```bash
docker build \
  -f docker/Dockerfile \
  -t algorithm-facerec:local \
  .
```

## 准备运行目录

```text
/opt/algorithm-operators/facerec/
├── config.toml
├── logs/
└── uploaded_faces/
```

`config.toml` 必须包含可连接的 MongoDB 配置。GPU 部署时将 `[gpu].device` 设为
`cuda:0`，并将 `[runtime].require_gpu` 设为 `true`。容器内服务端口固定为 `8000`，下面
单独部署示例将其映射到项目约定的宿主机端口 `8003`。

## 启动容器

```bash
docker run -d \
  --name facerec-gpu0 \
  --restart unless-stopped \
  --gpus '"device=0"' \
  -p 8003:8000 \
  -v /opt/algorithm-operators/facerec/config.toml:/config/config.toml:ro \
  -v /opt/algorithm-operators/facerec/logs:/app/logs \
  -v /opt/algorithm-operators/facerec/uploaded_faces:/app/uploaded_faces \
  -e CONFIG_PATH=/config/config.toml \
  -e PORT=8000 \
  -e UVICORN_WORKERS=1 \
  algorithm-facerec:local
```

本地 CPU 配置可以删除 `--gpus` 参数，并保持 `gpu.device="cpu"` 和
`runtime.require_gpu=false`。

接入调度平台时，还要加入平台网络并提供实例注册信息：

```bash
docker run -d \
  --name facerec-gpu0 \
  --restart unless-stopped \
  --network algorithm-platform \
  --gpus '"device=0"' \
  -p 127.0.0.1:18003:8000 \
  -v /opt/algorithm-operators/facerec/config.toml:/config/config.toml:ro \
  -v /opt/algorithm-operators/facerec/logs:/app/logs \
  -v /opt/algorithm-operators/facerec/uploaded_faces:/app/uploaded_faces \
  -e CONFIG_PATH=/config/config.toml \
  -e PORT=8000 \
  -e UVICORN_WORKERS=1 \
  -e PLATFORM_INSTANCE_ID=facerec-gpu0 \
  -e PLATFORM_SERVICE_URL=http://facerec-gpu0:8000 \
  -e PLATFORM_GPU_ID=0 \
  -e PLATFORM_OPERATOR_REGISTRY_TOKEN=REPLACE_WITH_TOKEN \
  algorithm-facerec:local
```

平台模式使用的 TOML 需要启用注册，并把 `platform.control_service_url` 指向同一 Docker
网络中的 Control Service。FaceRec 仍然只启动一个 Uvicorn worker；
`threading.max_workers` 只控制算子内部 Dlib 进程池。

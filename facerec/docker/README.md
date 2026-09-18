# FaceRec Docker 部署

所有构建命令都从 FaceRec 项目根执行，构建上下文为 `.`。镜像入口包装器固定启动
`python -m uvicorn app.main:app`，容器内端口为 `8000`，Uvicorn worker 必须为 1。
同一包装器也是 `multiprocessing` 的 spawn executable，使主进程和 InsightFace 检测
worker 在 NVML 中都显示为 `facerec`，同时继续保持进程隔离。

## 构建

```bash
DOCKER_BUILDKIT=1 docker build \
  -f docker/Dockerfile \
  -t algorithm-facerec:local \
  .
```

构建上下文必须包含七个 `ai_models/` 模型和 `wheel/` 中的共享 registry client wheel；
`.dockerignore` 会排除 `.git`、日志、媒体、测试 cache、部署 tar 和凭据。构建复用默认
BuildKit layer cache，不使用 `--no-cache`，不执行任何 Docker prune。

使用受控 HTTP 内网缓存提供 FastDeploy wheel 时，同时显式指定可信主机：

```bash
DOCKER_BUILDKIT=1 docker build \
  --build-arg FASTDEPLOY_FIND_LINKS=http://172.17.0.1:18765/ \
  --build-arg FASTDEPLOY_TRUSTED_HOST=172.17.0.1 \
  -f docker/Dockerfile \
  -t algorithm-facerec:local \
  .
```

Dockerfile 会先从该 `find-links` 以 `--no-index` 单独安装固定版本的 FastDeploy，
避免同版本的公网 wheel 覆盖内网缓存。`FASTDEPLOY_TRUSTED_HOST` 只用于明确受控的
HTTP 缓存；默认 HTTPS wheel 索引不需要该参数。

## 本地 CPU 容器

准备一个 `gpu.device="cpu"`、`runtime.require_gpu=false` 且可访问 MongoDB 的配置：

```bash
docker run --rm \
  --name facerec-local \
  -p 8003:8000 \
  -v "$PWD/config.toml:/app/config.toml:ro" \
  -v "$PWD/logs:/app/logs" \
  -v "$PWD/media:/app/media" \
  -e CONFIG_PATH=/app/config.toml \
  -e PLATFORM_INSTANCE_ID=facerec-local \
  -e PORT=8000 \
  -e UVICORN_WORKERS=1 \
  algorithm-facerec:local
```

## 严格 GPU 容器

GPU 配置必须设置 `gpu.device="cuda:0"` 与 `runtime.require_gpu=true`。容器内只暴露目标
物理卡，因此三个实例都使用逻辑 `cuda:0`，物理卡身份由 Docker 和平台 label 指定。

```bash
docker run -d \
  --name facerec-gpu0 \
  --restart unless-stopped \
  --network algorithm-platform \
  --gpus '"device=0"' \
  -p 127.0.0.1:18003:8000 \
  -v /opt/algorithm-operators/facerec/config.toml:/app/config.toml:ro \
  -v /opt/algorithm-operators/facerec/logs:/app/logs \
  -v /opt/algorithm-operators/facerec/media:/app/media \
  -e CONFIG_PATH=/app/config.toml \
  -e PLATFORM_INSTANCE_ID=facerec-gpu0 \
  -e PLATFORM_SERVICE_URL=http://facerec-gpu0:8000 \
  -e PLATFORM_GPU_ID=0 \
  -e PLATFORM_OPERATOR_REGISTRY_TOKEN=REPLACE_AT_RUNTIME \
  -e PORT=8000 \
  -e UVICORN_WORKERS=1 \
  algorithm-facerec:local
```

生产配置需要启用 `[platform].registration_enabled=true`，并将
`control_service_url` 指向同一平台网络中的 Control Service。FaceRec 不配置或连接 Redis。

## 验证

```bash
curl http://127.0.0.1:18003/ops/health
curl http://127.0.0.1:18003/ops/metadata
curl http://127.0.0.1:18003/ops/status
docker inspect facerec-gpu0 --format '{{json .State.Health}}'
docker exec facerec-gpu0 nvidia-smi
```

验收必须确认 InsightFace worker provider 和 ArcFace 配置设备均为 CUDA，MongoDB ready，
`operator_code=facerec`、`capabilities=["recognize"]`、声明容量和实例 label 正确，并完成真实
录入与识别。宿主机 `nvidia-smi --query-compute-apps=pid,process_name` 中，映射到 FaceRec
容器的所有 CUDA PID 都必须精确显示 `facerec`，不得显示 Python 或内部解释器绝对路径。
任一后端落到 CPU 时不得把实例标记为 ready。

正式平台固定使用镜像 repository `algorithm-facerec` 和实例名 `facerec-gpu0/1/2`。
新三实例、注册、真实租约、Online Gateway 路由和清理前 Smoke 全部通过后，才可按预先
记录的完整 ID 精确删除被替换的旧 FaceRec 容器和镜像。不得删除卷、模型、BuildKit cache、
平台中间件或其他算子资产。

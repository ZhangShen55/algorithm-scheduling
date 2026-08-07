# VBas Docker 部署

所有命令从 `vbas/` 项目根目录执行，构建上下文为 `.`。镜像内应用入口统一为 `app.main:app`，配置为 `/workspace/config.toml`，模型为 `/workspace/models` 或 `/workspace/models-encrypted`。

## 普通镜像

```bash
docker build -f docker/Dockerfile -t vbas:6.0 .
docker run --rm --name vbas-8981 \
  -p 8981:8981 \
  -e CONFIG_PATH=/workspace/config.toml \
  -v "$PWD/config.toml:/workspace/config.toml:ro" \
  -v "$PWD/models:/workspace/models:ro" \
  vbas:6.0 \
  python -m uvicorn app.main:app --host 0.0.0.0 --port 8981
```

`docker/Dockerfile.cuda113` 用于需要 CUDA 11.3/Python 3.8 兼容的环境。

## 源码保护镜像

```bash
docker build -f docker/Dockerfile \
  --build-arg PROTECT_SOURCE=1 \
  -t vbas:6.0-protected .
```

## Secure Runtime

1. 生成密钥和加密模型：

```bash
mkdir -p docker/secrets
python scripts/protect_tias_models.py \
  --source-dir models \
  --target-dir models-encrypted \
  --key-file docker/secrets/tias_model_key \
  --generate-key
chmod 0400 docker/secrets/tias_model_key
```

2. 构建并检查最小运行镜像：

```bash
docker build -f docker/Dockerfile.runtime -t vbas:6.0-secure .
python scripts/check_tias_runtime_image.py --image vbas:6.0-secure
```

3. 运行时挂载：

- `config.toml` → `/workspace/config.toml:ro`
- `models-encrypted/` → `/workspace/models-encrypted:ro`
- `models/cmu_panoptic_coco.yaml` → `/workspace/model-assets/cmu_panoptic_coco.yaml:ro`
- `docker/secrets/tias_model_key` → `/run/bootstrap-secrets/tias_model_key:ro`

secure entrypoint 会将密钥复制到 `/dev/shm/tias_model_key`，应用读取后删除运行期副本。宿主机密钥是容器重建/重启的必要恢复材料，不得在部署后随意删除。

## Compose

```bash
docker compose -f docker/docker-compose.yml config
docker compose -f docker/docker-compose.yml up --build
```

GPU 与 secure 变体：

- `docker/docker-compose.gpu.yml`
- `docker/docker-compose.gpu.128.yml`
- `docker/docker-compose.gpu.secure.yml`

这些 Compose 文件保留现有 `tias-*` 实例名、`[TIAS]` 配置和 `TIAS_*` 环境变量，因为它们是已有注册/部署兼容标识，不属于本次目录结构整改。

## 网络边界

VBas 可单独启动并直接推理。如需注册/心跳，`[TIAS].AiQualityBaseUrl` 需指向外部 `jy-vision-orchestrator-server` 的兼容入口，并保证两个容器网络可达。

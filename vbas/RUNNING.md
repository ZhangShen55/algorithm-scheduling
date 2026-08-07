# VBas 运行手册

## 职责边界

VBas 只提供学生/教师图片推理、实例准入控制、运行状态与现有注册心跳。外部 `jy-vision-orchestrator-server` 负责视频抽帧、调度、反复精细检测、聚合和入库。

## 本地 CPU 启动

1. 确认 `config.toml` 中 `GPU_ID = "cpu"` 且模型位于 `models/`。
2. 在项目根目录执行：

```bash
conda activate jy-tias
pip install -r requirements_mac.txt
export CONFIG_PATH="$PWD/config.toml"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8981
```

3. 验证健康：

```bash
curl -fsS http://127.0.0.1:8981/AE/Health
curl -fsS http://127.0.0.1:8981/AE/WorkerStatus
```

## 直接推理

学生接口：

```bash
curl -sS http://127.0.0.1:8981/ImageDetect/student/v1.0.0 \
  -H 'Content-Type: application/json' \
  -d '{"ImageList":[{"StoragePath":"/absolute/path/image.jpg","ImageId":"student-1"}]}'
```

教师接口：

```bash
curl -sS http://127.0.0.1:8981/ImageDetect/teacher/v1.0.0 \
  -H 'Content-Type: application/json' \
  -d '{"ImageList":[{"StoragePath":"/absolute/path/image.jpg","ImageId":"teacher-1"}],"ReturnHeadPose":false}'
```

## 并发与排空

- `[TIAS].MaxConcurrentBatches`：单实例同时推理批次数。
- `[TIAS].MaxQueueSize = 0`：满载时不在本算子无限排队。
- `GET /AE/WorkerStatus`：查看运行批次和队列。
- `PUT /AE/Drain`：停止接受新批次，便于下线实例。

## 多实例启动脚本

`docker/start.sh` 读取根级 `config.toml` 的 `INSTANCE_COUNT` 和 `WORKERS_PER_INSTANCE`。单实例对外默认端口为 `8881`；多实例在 `8981...` 启动 Uvicorn，由 Nginx 在 `8881` 转发。

```bash
export CONFIG_PATH="$PWD/config.toml"
bash docker/start.sh
```

## Docker

从 `vbas/` 根目录构建：

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

Compose 文件位于 `docker/`，具体见 [docker/README.md](docker/README.md)。

## 加密模型

```bash
mkdir -p docker/secrets
python scripts/protect_tias_models.py \
  --source-dir models \
  --target-dir models-encrypted \
  --key-file docker/secrets/tias_model_key \
  --generate-key
chmod 0400 docker/secrets/tias_model_key
```

secure runtime 配置：

```toml
[ModelProtection]
Enabled = true
EncryptedModelRoot = "/workspace/models-encrypted"
DecryptedTempRoot = "/dev/shm/tias-models"
KeyFile = "/dev/shm/tias_model_key"
CleanupAfterLoad = true
```

```bash
docker build -f docker/Dockerfile.runtime -t vbas:6.0-secure .
python scripts/check_tias_runtime_image.py --image vbas:6.0-secure
```

## 故障检查

- 启动时提示模型不存在：检查 `models/`、`CONFIG_PATH` 及 `[ModelProtection]`。
- DirectMHP 失败：检查 `app/vendor/DirectMHP/`、权重、YAML 及 `Teacher_Head_Pose.Enabled`。
- 满载返回 busy：查看 `/AE/WorkerStatus`，由上层选择其他实例或重试。
- 注册心跳失败：检查 `[TIAS].AiQualityBaseUrl` 是否指向已抽离的视觉编排服务入口；该失败不应阻止本地直接推理调试。

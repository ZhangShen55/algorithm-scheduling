# GPU Metrics Exporter

独立的只读 GPU 观测容器。通过 NVIDIA Management Library 读取宿主机所有可见 GPU，并提供：

- `GET /health`
- `GET /gpu`：控制台使用的 JSON 快照
- `GET /metrics`：Prometheus 文本指标

每台 GPU 主机必须设置 `GPU_EXPORTER_HOST_ID`，值为该主机固定的内网 IPv4。`/health` 和 `/gpu`
会返回该值，`/metrics` 也会将它作为 `host_id` 标签。Exporter 只报告本机 GPU，不能用容器名推断主机。

启动要求：宿主机已安装 NVIDIA 驱动和 NVIDIA Container Toolkit。该容器使用全部 GPU 仅用于采集，不改变现有算子容器的 `NVIDIA_VISIBLE_DEVICES` 单卡隔离，也不挂载 Docker socket。

```bash
docker compose \
  -f ../algorithm-scheduling-platform/deploy/docker-compose.gpu-metrics.yml \
  up -d --build
```

例如在 `192.168.29.12` 上启动前设置：

```bash
export GPU_EXPORTER_HOST_ID=192.168.29.12
docker compose -f ../algorithm-scheduling-platform/deploy/docker-compose.gpu-metrics.yml up -d --build
```

默认监听 `9400`。接口带宽较小，控制台默认每 5 秒读取一次，可在页面配置中调整为 1～30 秒。

服务器无法访问 Python 包源时，可在构建前把与 `requirements.txt` 匹配的通用 wheel 放入临时构建
目录 `gpu_metrics_exporter/wheels/`。Dockerfile 检测到 wheel 后使用 `--no-index` 离线安装；仓库只
保留空目录，不提交第三方二进制包。

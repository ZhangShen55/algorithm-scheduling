# GPU Metrics Exporter

独立的只读 GPU 观测容器。通过 NVIDIA Management Library 读取宿主机所有可见 GPU，并提供：

- `GET /health`
- `GET /gpu`：控制台使用的 JSON 快照
- `GET /metrics`：Prometheus 文本指标

启动要求：宿主机已安装 NVIDIA 驱动和 NVIDIA Container Toolkit。该容器使用全部 GPU 仅用于采集，不改变现有算子容器的 `NVIDIA_VISIBLE_DEVICES` 单卡隔离，也不挂载 Docker socket。

```bash
docker compose \
  -f ../algorithm-scheduling-platform/deploy/docker-compose.gpu-metrics.yml \
  up -d --build
```

默认监听 `9400`。接口带宽较小，控制台默认每 5 秒读取一次，可在页面配置中调整为 1～30 秒。

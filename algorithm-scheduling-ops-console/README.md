# Algorithm Scheduling Ops Console

TypeScript + React + Vite 的内部只读运维工具，默认直接通过浏览器读取 Control Service、online-gateway-service 和独立 GPU exporter。控制台不发布任务、不写 Kafka、不控制 Docker 容器。

## 本地开发

```bash
npm ci
npm run dev
```

默认地址为 `http://127.0.0.1:5174`。Vite 开发代理支持：

- `/control` -> `http://127.0.0.1:18100`
- `/gateway` -> `http://127.0.0.1:8001`

GPU exporter 通常直接配置为 `http://127.0.0.1:9400`。可以通过 `VITE_CONTROL_BASE_URL`、`VITE_GATEWAY_BASE_URL` 和 `VITE_GPU_BASE_URL` 设置默认地址。

## Docker 启动

```bash
VITE_CONTROL_BASE_URL=http://192.168.29.11:18100 \
VITE_GATEWAY_BASE_URL=http://192.168.29.11:18103 \
VITE_GPU_BASE_URL=http://192.168.29.11:9400 \
docker compose up -d --build
```

容器只负责提供静态页面，默认映射到 `http://服务器IP:5174`。通过页面顶部配置按钮填写：

```text
Control Service:       http://192.168.29.11:18100
gateway-online:        http://192.168.29.11:18103
GPU exporter:          http://192.168.29.11:9400
```

端口和绑定地址可以通过 `OPS_CONSOLE_PORT`、`OPS_CONSOLE_BIND_HOST` 修改。Control Service、online-gateway-service 和 GPU exporter 已提供内部工具需要的 CORS/OPTIONS 响应，因此不强制要求额外 Nginx 反向代理。
上述 `VITE_*_BASE_URL` 只设置镜像首次打开时的默认地址；页面保存的浏览器配置优先，修改地址不需要重新构建镜像。

## 页面数据

- **运行总览**：实例、容量、队列、网关请求、任务发布和 Kafka 消费积压。
- **算子实例**：统一实例清单，包含算子、GPU、模型、容量、心跳、有效租约和多条件筛选。
- **实例详情**：显示当前有效租约的 `task_id`、`work_type`、`node_id`、`item_id` 和来源服务。
- **任务追踪**：默认按 `updated_at desc` 读取最新任务，支持分页、排序和 `task_id` 详情。
- **网关流量**：读取 online-gateway-service `/metrics`，展示请求、错误、延迟和容量拒绝。
- **系统状态**：展示 Control Service 就绪、存储、任务发布/Kafka 和 GPU exporter 状态。

总览默认刷新 10 秒，实例租约默认刷新 5 秒，GPU 默认刷新 5 秒。刷新秒数可以在连接与观测配置中调整，范围为 1～60 秒（实例租约和 GPU 为 1～30 秒），配置只保存在当前浏览器。

## GPU exporter

在目标服务器启动独立 GPU 容器：

```bash
docker compose \
  -f ../algorithm-scheduling-platform/deploy/docker-compose.gpu-metrics.yml \
  up -d --build
```

该容器通过 NVIDIA Management Library 读取所有可见 GPU，提供 `/health`、`/gpu` 和 `/metrics`。它需要宿主机 NVIDIA 驱动、NVIDIA Container Toolkit 和可用的 GPU；现有算子容器仍保持每个实例单卡隔离，不改为 `--gpus all`。

## 真实数据排查

```bash
curl http://192.168.29.11:18100/health
curl http://192.168.29.11:18100/ops/operator-instances
curl 'http://192.168.29.11:18100/ops/course-jobs?page=1&page_size=10&sort_by=updated_at&order=desc'
curl http://192.168.29.11:18103/metrics
curl http://192.168.29.11:9400/gpu
```

如果页面提示 CORS，先确认服务已经重建到包含 CORS middleware 的版本；如果任务列表返回 `404`，说明远端 Control Service 还未部署包含 `/ops/course-jobs` 的版本。GPU 不可用时不影响其他 Control Service 和网关数据展示。

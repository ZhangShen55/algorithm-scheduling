## Why

当前运维控制台已经具备读取 Control Service 和 online-gateway-service 的前端原型，但仍以 `ops-console` 目录存在，缺少可直接交付的独立 Docker 静态工具。真实环境中浏览器直连服务还会受到跨域预检限制，远端 Control Service 也尚未发布本地已有的任务列表接口，导致任务总览无法稳定使用真实数据。

同时，算子实例容量对比与实例清单存在重复信息。运维人员更需要在一个可筛选的实例清单中同时看到容量、心跳、生命周期和当前任务归属，并能继续按 `task_id`、`task_type`、`work_type` 定位任务流转，而不是在两个页面之间切换。

## What Changes

- 将前端项目目录从 `ops-console` 更名为 `algorithm-scheduling-ops-console`，同步包名、README、构建和引用路径。
- 建立规范的 TypeScript + React + Vite Docker 项目结构，提供多阶段 `Dockerfile`、轻量静态文件服务、Compose 部署文件、`.dockerignore` 和生产部署说明。
- 明确真实数据访问契约：Control Service 提供实例、容量、队列、任务列表/详情、实例活跃租约、Kafka 聚合和就绪状态；online-gateway-service 提供 `/metrics`；控制台直接填写 IP/端口，三个观测服务提供 CORS/OPTIONS 支持。
- 增加独立 `gpu_metrics_exporter` 容器，读取宿主机全部 GPU 的利用率、显存、温度、功耗和进程数，并提供 JSON/Prometheus 只读接口。
- 将本地已有的 `GET /ops/course-jobs` 任务列表接口纳入 Control Service 发布、迁移、Smoke 和兼容性验证，默认按 `updated_at desc` 分页返回最新任务。
- 将“实例容量对比”并入“实例清单”，保留容量使用率和筛选能力；点击实例展示当前有效租约对应的 `task_id`、`work_type`、`node_id`、`item_id` 及上下文状态。
- 保持第一阶段只读观测，不新增 Docker 控制、排空、恢复上线、重启、鉴权或审计操作，不修改 A 服务既有 HTTP/WebSocket 契约。

## Capabilities

### New Capabilities

- `algorithm-ops-observability`: 为密集型运维控制台定义真实数据读取、实时刷新、任务分页、实例筛选、实例任务归属和网关指标展示要求。
- `algorithm-ops-console-deployment`: 定义前端项目命名、生产构建产物、Docker 静态部署和配置注入要求。

### Modified Capabilities

- 无。当前 `openspec/specs/` 中没有可直接复用的控制台或运维观测能力规范；现有四个平台服务规范不改变其服务级要求。

## Impact

- 前端：`ops-console/` 全目录迁移为 `algorithm-scheduling-ops-console/`，调整 React 页面信息架构、实例清单和真实接口错误状态，保留已有四套视觉风格及 ECharts。
- Control Service：发布并验证已有的 `GET /ops/course-jobs`，增加只读 `GET /ops/kafka` 聚合 orchestrator 发布指标并补充 CORS；不改 `/api/course-jobs` 等 A 服务接口。
- online-gateway-service：继续复用现有 `GET /metrics`，增加 CORS/OPTIONS，不增加在线业务路由。
- 部署：新增控制台 Docker/Compose 资产和独立 GPU exporter；远端部署需要重新构建并重启 Control Service 才能使用任务列表和 Kafka 聚合。
- 验证：增加接口契约、CORS/同源代理、容器健康检查、真实数据加载和前端构建验证；不要求改动 A 服务或七类算子协议。

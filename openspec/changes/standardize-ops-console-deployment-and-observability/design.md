## Context

当前 `ops-console` 是一个可运行的 React + TypeScript + Vite 原型，前端已经调用以下只读数据源：

| 数据 | 服务 | 接口 |
| --- | --- | --- |
| 算子实例 | Control Service | `GET /ops/operator-instances` |
| 容量快照 | Control Service | `GET /ops/operator-instances/snapshot` |
| 队列 | Control Service | `GET /ops/queues` |
| 存储 | Control Service | `GET /ops/storage` |
| 就绪状态 | Control Service | `GET /ops/readiness` |
| 最新课程任务 | Control Service | `GET /ops/course-jobs` |
| 任务详情 | Control Service | `GET /ops/course-jobs/{task_id}` |
| 实例当前任务 | Control Service | `GET /ops/operator-instances/{instance_id}/active-leases` |
| 网关指标 | online-gateway-service | `GET /metrics` |
| Kafka 发布与积压 | Control Service 聚合 | `GET /ops/kafka` |
| GPU 状态 | gpu_metrics_exporter | `GET /gpu` |

本地 Control Service 已实现任务列表接口，但远程 `192.168.29.11:18100` 尚未部署该版本。两个后端当前可达，但浏览器直连没有 CORS 响应头且预检为 `405`。因此“读取失败”主要是浏览器访问边界和服务版本不一致，不是 Docker 容器本身无法启动。

变更需要同时覆盖前端项目资产、部署入口和运维信息架构，同时必须遵守 A 服务与七类算子的既有 HTTP/WebSocket 契约。

## Goals / Non-Goals

**Goals:**

- 让控制台默认通过真实只读接口工作，并清楚标识实时、演示和失败状态。
- 把任务列表、任务详情和实例活跃租约组合成可操作的观测链路：实例 -> `task_id`/`work_type` -> 任务详情。
- 把容量信息集中到实例清单，在同一张表中完成算子、生命周期、模型、设备和活动任务筛选。
- 将前端目录改为 `algorithm-scheduling-ops-console`，提供可复现的 Docker 静态镜像和页面内地址配置。
- 让 Control Service 的 `/ops/course-jobs` 发布版本、部署检查和前端分页参数一致。

**Non-Goals:**

- 不新增或修改 A 服务的 `/api/course-jobs`、在线 HTTP/WebSocket 路径、请求字段和响应字段。
- 不在控制台实现排空、恢复上线、重启容器、注册、心跳、租约写入或任何 Docker 控制。
- 第一阶段不引入 Prometheus 服务端查询层；网关短期趋势继续由浏览器按刷新采样，历史窗口留给后续能力。
- 不把 PostgreSQL、Redis、Kafka、模型路径、容量声明、租约 TTL 或容器参数暴露为浏览器可编辑配置。

## Decisions

### 1. 内部工具采用直接跨域读取

控制台容器只提供静态页面，浏览器直接请求用户填写的 Control Service、online-gateway-service 和 GPU exporter 地址。三个服务对内部工具提供 `Access-Control-Allow-Origin: *`、GET/OPTIONS 和非凭据模式，满足当前低安全要求，避免增加反向代理配置。

**备选方案：** 使用 Nginx 或其他同源代理。它可以收敛浏览器 Origin，但增加一个部署组件；用户已明确当前工具优先简单部署，因此不作为本次默认方案。

### 2. 只增加一个 Kafka 观测聚合入口

Control Service 的 `/ops/course-jobs` 已经按数据库课程任务聚合，并支持 `page`、`page_size`、`sort_by` 和 `order`；任务详情和实例活跃租约接口也已存在。实现阶段只需确保远程镜像包含这些代码、数据库迁移已完成、Compose/Smoke 覆盖这些 GET 请求，不重复创造 A 服务接口。

Control Service 增加只读 `/ops/kafka`，读取自身 Outbox 队列快照并在内部请求 orchestrator-service `/metrics`，避免把 loopback-only 的 18101 暴露给浏览器。`online-gateway-service` 继续复用现有 `/metrics` 文本指标。Prometheus 聚合查询属于后续历史趋势增强。

### 3. 实时性采用分层刷新而非 WebSocket 改造

总览、实例清单、网关指标和系统状态共享默认 10 秒刷新；实例详情的 active lease 使用默认 5 秒刷新；任务列表在分页、排序或手动刷新时读取，任务详情在进入详情和手动刷新时读取。所有请求带取消/过期保护，避免切换页面后旧响应覆盖新状态。

该方案满足当前运维观测的分钟内状态感知，不改变任何服务协议。后续若需要秒级事件流，可新增 SSE/WebSocket 观测通道，但不在本次范围内。

### 4. 实例页面以清单为唯一主视图

移除独立的“实例容量对比”导航/面板，将容量使用率作为“实例清单”的表格列和顶部汇总指标。清单提供算子类型、生命周期、模型就绪、设备/GPU、是否有活动任务筛选；按容量使用率、最近心跳和实例 ID 排序。点击行打开详情抽屉，展示 `task_id`、`work_type`、`node_id`、`item_id`、`work_id`、租约状态、获取时间和过期时间。

在线网关租约可能没有 `task_id`，页面显示“在线请求/无课程任务”而不是伪造课程任务归属；存在 `task_id` 时可跳转任务详情。

### 5. 真实数据模式与演示数据隔离

生产构建默认使用实时模式。首次加载期间显示加载态；读取失败显示失败原因和重试，不把演示快照标成实时数据。演示数据只通过明确的开发开关启用，并在页面顶部持续显示“演示数据”。连接配置仍可在浏览器中保存 Control Service 和 gateway-online 的协议、IP、端口及刷新周期，但默认值为同源 `/control`、`/gateway`。

### 6. Docker 使用多阶段构建，运行时只保留静态站点

第一阶段使用锁定的 `package-lock.json` 执行 `npm ci` 和 `npm run build`；第二阶段使用 Node 运行轻量静态文件服务，只复制 `dist/` 和服务脚本。Compose 只负责控制台容器，不把后端源码复制进控制台镜像。GPU exporter 单独使用 GPU 运行时，不与算子容器共享业务代码。项目根保留 `src/`、`public/`、配置、Docker 资产和部署 README；`node_modules/`、`dist/`、测试报告不作为交付源文件。

## Risks / Trade-offs

- [风险] 远端 Control Service 镜像未更新时，实例接口可用但 `/ops/course-jobs` 返回 `404`。→ [缓解] 发布 Smoke 固定检查列表、详情和分页接口；页面将接口错误明确显示为后端版本/地址问题。
- [风险] 后端地址、端口或 CORS 配置错误会让页面观测失败。→ [缓解] 页面提供地址配置和“测试读取”，README 提供 curl/OPTIONS 排查；容器健康检查只验证静态站点，不伪装后端健康。Nginx/BFF 仍可作为后续同源部署选项，但不是本阶段前置条件。
- [风险] `/metrics` 是进程累计值，浏览器刷新采样得到的速率在刷新间隔变化时会失真。→ [缓解] 记录采样时间并用相邻采样差值计算；页面标注“会话采样”，不宣称为 Prometheus 历史数据。
- [风险] active lease 与算子心跳存在短暂时差，导致 `reported_inflight` 与租约数量不一致。→ [缓解] 同时展示两个字段和差值，使用租约 `work_context` 作为任务归属来源，不自行推断历史归属。
- [风险] 目录重命名会影响未提交的本地引用。→ [缓解] 先保留所有源文件内容，再全局搜索 `ops-console` 路径/存储键/文档引用；localStorage key 可兼容读取旧键后迁移到新键。

## Migration Plan

1. 在本地完成前端目录重命名、生产 Docker 资产和合并后的实例页面，执行 `npm ci`、`npm run build` 和容器静态资源检查。
2. 在目标环境先完成 Control Service 数据库迁移，再用包含 `/ops/course-jobs` 的同一 Git SHA 构建并重启 Control Service；确认 `/health`、`/ops/readiness`、实例接口、任务列表接口和 active lease 接口。
3. 构建控制台镜像并启动 Compose；首次打开页面后，在“连接与观测配置”中填写 `http://192.168.29.11:18100`、`http://192.168.29.11:18103` 和 `http://192.168.29.11:9400`，再执行“测试读取”。
4. 回滚时保留后端服务版本，停止并替换控制台镜像即可；若回滚到不包含 `/ops/course-jobs` 的前端，页面必须显示接口不可用，不得回退为看似真实的演示数据。

## Open Questions

- 生产环境后续是否需要用 Nginx/BFF 收敛到同源入口；本阶段默认独立静态容器加服务端宽松 CORS。
- 是否需要在后续阶段为网关接入 Prometheus 查询层，以提供跨浏览器会话的 1 小时/24 小时历史趋势。
- 目标环境是否允许浏览器访问 `192.168.29.11` 宿主机端口；若不允许，需将控制台和观测服务放入可达网络，或后续增加同源代理。

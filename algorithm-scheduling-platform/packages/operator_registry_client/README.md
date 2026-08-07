# 算子注册客户端接入要求

每个独立进程/端口/GPU 端点是一个 `instance_id`，并使用
`OperatorRegistryClient` 在模型加载成功后注册。算子至少挂载以下运行面接口：

- `GET /ops/health`：只表示 HTTP 进程存活，不代表模型已经就绪。
- `GET /ops/status`：返回 `lifecycle`、`model_ready`、`inflight` 和
  `declared_capacity`；平台只路由 `ONLINE + model_ready=true` 的实例。
- `POST /ops/drain`：把本地状态切换为 `DRAINING`，拒绝新任务，存量任务可继续完成。

推荐使用 `create_operator_ops_router` 直接挂载统一路由。服务启动后调用 `register()` 并运行
周期 `heartbeat()`；关闭前先调用 `drain()`，等待本地 `inflight=0` 后再 `unregister()`。
注册客户端不改变现有模型推理接口、请求和响应。

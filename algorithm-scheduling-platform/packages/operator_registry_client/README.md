# 算子注册客户端接入要求

该目录可独立构建为 `algorithm-operator-registry-client` wheel，支持 Python 3.10 及以上版本。它只包含算子运行面、注册、心跳和排空逻辑，不依赖平台的 PostgreSQL、Redis、Kafka、仓储或状态机包。

```bash
python -m pip wheel --no-deps --wheel-dir dist .
python -m pip install dist/algorithm_operator_registry_client-0.1.0-py3-none-any.whl
```

各算子的 `requirements.txt` 显式固定 `algorithm-operator-registry-client==0.1.0`。
内部 PyPI 尚未建立时，先安装或暂存上述 wheel，再安装算子 requirements；不得从公网查找同名私有包。

安装后保持现有导入路径：

```python
from packages.operator_registry_client import install_operator_runtime
```

每个独立进程/端口/GPU 端点是一个 `instance_id`，并使用
`OperatorRegistryClient` 在模型加载成功后注册。算子至少挂载以下运行面接口：

- `GET /ops/health`：只表示 HTTP 进程存活，不代表模型已经就绪。
- `GET /ops/status`：返回 `lifecycle`、`model_ready`、`inflight` 和
  `declared_capacity`；平台只路由 `ONLINE + model_ready=true` 的实例。
- `POST /ops/drain`：把本地状态切换为 `DRAINING`，拒绝新任务，存量任务可继续完成。

推荐使用 `create_operator_ops_router` 直接挂载统一路由。服务启动后调用 `register()` 并运行
周期 `heartbeat()`；关闭前先调用 `drain()`，等待本地 `inflight=0` 后再 `unregister()`。
注册客户端不改变现有模型推理接口、请求和响应。

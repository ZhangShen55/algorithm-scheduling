# 算子注册客户端接入要求

该目录可独立构建为 `algorithm-operator-registry-client` wheel，支持 Python 3.10 及以上版本。它只包含算子运行面、注册、心跳和排空逻辑，不依赖平台的 PostgreSQL、Redis、Kafka、仓储或状态机包。

```bash
cd ../..
python scripts/build_and_stage_operator_registry_wheel.py
python -m pip install \
  packages/operator_registry_client/dist/algorithm_operator_registry_client-0.2.0-py3-none-any.whl
```

构建脚本要求 Git 跟踪集合严格等于 `pyproject.toml`、`README.md` 和显式声明的七个
Python 模块；新增模块必须先更新构建合同，不能自动进入制品。脚本将这些普通文件复制
到临时 clean source tree，不会携带工作树中的 `build/`、`__pycache__/`、`.pyc` 或
未跟踪内部文件。然后使用当前 Python 环境中已安装的构建
后端，以 `--no-deps --no-build-isolation --no-index` 在受控临时 wheelhouse 中生成
制品。脚本严格校验固定文件名、Name、Version、Requires-Python、Requires-Dist、
wheel 成员 allowlist 和 RECORD hash/size 后，才原子更新本目录的 `dist/`，并将同一
字节分发到八个算子项目的 `wheel/` 构建上下文。任一目标发布或最终 SHA-256 校验失败
时恢复全部旧版本，不会留下混合制品。发布阶段使用 `dist/` 下固定私有文件锁串行化
进程，并将每个目标的临时文件、备份和替换进度写入耐久事务 journal；进程崩溃后，
下一次运行会在发布新版本之前优先完成旧版本回滚。

各当前七个算子的 `requirements.txt` 显式固定 `algorithm-operator-registry-client==0.2.0`。
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

推荐使用 `create_operator_ops_router` 直接挂载统一路由。注册开关、Control Service 地址、
心跳周期、平台注册容量和 GPU 强制检查只从算子所选 TOML 的 `[platform]`、`[runtime]`
读取，并由算子显式传给 `install_operator_runtime`。启用主动注册时必须同时通过运行环境
配置 `PLATFORM_OPERATOR_REGISTRY_TOKEN`、实例 ID 和服务 URL。
服务启动后调用 `register()` 并运行周期 `heartbeat()`；正常进程关闭直接注销，不持久化
`DRAINING`。只有运维排空才显式调用 `drain()`，等待本地 `inflight=0` 后再注销；该排空意图
会跨实例重启保留。注册客户端不改变现有模型推理接口、请求和响应。

日志接入

`logging.py` 提供统一的 `FileLoggingSettings`、JSON Lines formatter、文件/stdout
双输出和大小加年龄轮转。默认写入每个项目的 `logs/{instance_id}/application.log`，
单文件上限 100 MiB，归档保留 7 日；调用 `configure_logging()` 前应完成模型和实例
标识解析。日志上下文采用允许列表，不能把 Base64、媒体字节、完整请求/响应、凭据或
完整 ASR/OCR 文本作为 message 或 extra 写入。

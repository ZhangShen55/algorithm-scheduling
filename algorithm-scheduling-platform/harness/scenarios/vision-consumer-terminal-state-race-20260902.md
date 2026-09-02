# Vision Consumer 终态竞态修复验证（2026-09-02）

## 变更范围

- OpenSpec：`fix-vision-consumer-terminal-state-race`
- 受影响服务：`vision_orchestrator_service`
- 受影响代码：`app/application/events.py`、`tests/test_runtime.py`
- 不涉及：数据库迁移、Kafka topic/消息格式、VBas 协议、HTTP/WebSocket 路由和 `text_analysis/`

## 修复前复现

使用内存 Repository 控制并发顺序：处理器初次读取节点为 `RUNNING`，进度写入前另一事务将节点推进为 `COMPLETED`，随后 Repository 拒绝进度更新。修复前异常被包装为 `_ProgressDeliveryError`，Consumer 未提交 offset 并退出；节点终态本身未被覆盖。

## 修复后的行为

- 进度更新发生 `RepositoryStateConflictError` 后，处理器重新读取节点。
- 二次读取确认 `COMPLETED`、`FAILED` 或 `CANCELLED` 时，抛出内部终态竞态信号，中止迟到分析，不覆盖节点、不发布覆盖性进度事件；Consumer 将消息视为成功并提交 offset。
- `complete_node()` 发生同类竞态时，确认任一终态后幂等结束，不重复写结果或发布事件。
- 二次读取仍为非终态、节点不存在、数据库读取失败、Kafka 发布失败等情况保持失败，不提交 offset。
- 终态竞态消息后面的正常消息继续处理，并按分区连续提交。

## 验证命令与结果

在工作区根目录执行：

```bash
algorithm-scheduling-platform/.venv/bin/python -m pytest -q vision_orchestrator_service/tests/test_runtime.py
algorithm-scheduling-platform/.venv/bin/python -m pytest -q orchestrator_service/tests/test_visual_runtime.py -k 'late_progress or progress_repository_error'
PYTHONPATH="$PWD/vision_orchestrator_service:$PWD/algorithm-scheduling-platform" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q vision_orchestrator_service/tests
algorithm-scheduling-platform/.venv/bin/ruff check \
  vision_orchestrator_service/app/application/events.py \
  vision_orchestrator_service/tests/test_runtime.py
algorithm-scheduling-platform/.venv/bin/python -m compileall -q vision_orchestrator_service/app
algorithm-scheduling-platform/.venv/bin/python -c \
  'from vision_orchestrator_service.app.main import app; print(type(app).__name__)'
```

结果：定向 Consumer 测试 `28 passed`；Orchestrator 视觉回归 `16 passed`；正确服务导入路径下视觉测试 `60 passed`；Ruff、`compileall` 和应用导入通过。

## 192.168.29.11 远端替换

- 仅同步 `vision_orchestrator_service/app/application/events.py`，未重建七个算法算子或其他三个平台服务。
- 使用既有 Docker BuildKit 缓存构建，未使用 `--no-cache`、`docker buildx prune` 或宽泛清理。
- 替换前容器完整 ID 为 `c6876b2eee649351454554df8bf2d1f0d408a775c1ad6ef59482cd7af1b923ed`（名称为 `algorithm-scheduling-platform-vision-orchestrator-service-1`），旧镜像完整 ID 为 `sha256:8fc408da75eacaf07ba3530f4476a2968de305d41ba695cb349185d0b0de92a0`；该镜像已在新容器健康后精确删除。
- 新容器完整 ID 为 `9efead38eddcd5bbfff16d0e34179fb3c3c22b600cdf07c7c11ff8aeb77e747e`，新镜像完整 ID 为 `sha256:3a14bf29765f8402efd0e4d7bb79508072ee1055813ff1bcfaa8614b3982346a`，镜像 revision 标签为 `8d490cb92317f6e0aa2ab45b89179c812c187966`。
- 远端 `GET http://127.0.0.1:18102/ready` 返回 `status=ready`，`visual_command_consumer`、PostgreSQL、Kafka、control-service 检查均为 ready；control、orchestrator、vision、online 四个平台容器均为 healthy。
- 旧镜像核验结果为不存在（`docker image inspect` 返回 1）。

## 修复后风险回归

- 本地将终态竞态、完成阶段竞态、非终态冲突和后续消息继续消费相关用例独立启动执行 100 次；每次 `8 passed`，累计 800 次断言通过。
- 远端 Vision 容器状态为 `running/healthy`，重启次数为 0；`/ready` 返回 ready，视觉 Consumer 活动成员正常。
- 远端 Kafka `algorithm-orchestrator`、`algorithm-orchestrator-visual-events` 和 `vision-orchestrator` 三个 Consumer Group 当前 lag 均为 0。
- 远端最近 30 分钟日志未出现 `RepositoryStateConflictError`、`fatal`、Consumer 退出或 `unhealthy`。
- 该验证未向生产数据库注入状态竞争，也未修改课程节点；它证明代码竞态夹具和部署后运行状态稳定，但不能替代真实生产事务注入压测。

## 验收结论

本次达到静态检查、单元/服务测试和远端 Vision 容器替换/就绪验证层级，已覆盖终态迟到进度、完成阶段竞态、非终态冲突、异常不提交 offset 与后续消息继续消费。远端验证确认服务已运行，但尚未执行真实竞态注入或完整业务负载回归。

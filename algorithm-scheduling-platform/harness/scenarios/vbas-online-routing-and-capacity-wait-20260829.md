# VBas 在线路由与容量等待验证

## 范围

本记录对应 OpenSpec 变更 `add-vbas-online-routing-and-capacity-wait`。验证在线教师、学生、
纯人数三个网关路由，在线/离线容量池隔离，实例内在线 FIFO 队列，以及容量暂不可用时的等待、
退避、超时和租约释放补位。

## 本地自动化验证

从工作区根目录执行：

```bash
PYTHONPATH="$PWD/online_gateway_service:$PWD/algorithm-scheduling-platform:$PWD" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q online_gateway_service/tests

PYTHONPATH="$PWD/vision_orchestrator_service:$PWD/algorithm-scheduling-platform:$PWD" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q vision_orchestrator_service/tests

PYTHONPATH="$PWD/vbas:$PWD/algorithm-scheduling-platform:$PWD" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  vbas/tests/test_capacity_pools.py vbas/tests/test_config_loader.py \
  vbas/tests/test_tias_api_surface.py vbas/tests/test_tias_worker_state.py

PYTHONPATH="$PWD:$PWD/algorithm-scheduling-platform" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  algorithm-scheduling-platform/tests/test_operator_deployment_integration.py \
  algorithm-scheduling-platform/tests/integration/test_redis_operator_registry.py

PYTHONPATH="$PWD:$PWD/algorithm-scheduling-platform" \
  algorithm-scheduling-platform/.venv/bin/python -m compileall -q \
  control_service/app orchestrator_service/app vision_orchestrator_service/app \
  online_gateway_service/app algorithm-scheduling-platform/packages
```

本次结果：Online Gateway `63 passed`，Vision `50 passed`，VBas 准入/配置/API `13 passed`，
部署契约和 Redis 注册集成 `43 passed`，平台服务与共享包 `compileall` 通过。FastAPI 的弃用警告
不影响测试结果。

## 关键行为断言

- `/online/vbas/teacher`、`/online/vbas/student`、`/online/vbas/person-count` 均按单个 HTTP 请求
  申请 `online` 租约，成功响应透传 VBas 原始 JSON。
- 单个实例 `MaxConcurrentOnlineRequests=24`、`MaxQueueOnlineSize=24` 时，运行 24 个、等待 24 个；
  队列满载返回 429，离线 batch 使用独立 `MaxConcurrentOfflineBatches`。
- 等待请求以 0.2 秒基础间隔退避并带抖动，最多等待 300 秒；超时不产生释放请求，不泄漏租约。
- 512 个在线模拟请求能够在三个实例各 24 个运行槽位下等待释放并全部完成，单实例峰值不超过 24。
- 注册中心不可用的 503 不伪装成容量不足；只有容量不足响应进入等待循环。

## 目标机验收状态

目标机为 `192.168.29.11`，需在批准发布 SHA 的新容器启动后补充：三实例注册/心跳、三类真实
图片请求、在线 512 并发、在线/离线混合容量隔离、健康检查和容器日志证据。验证必须同时记录
Control 租约时序、实例请求日志和 GPU 进程观察；`nvidia-smi` 只能作为 GPU 活跃补充，不能单独
证明路由均衡。

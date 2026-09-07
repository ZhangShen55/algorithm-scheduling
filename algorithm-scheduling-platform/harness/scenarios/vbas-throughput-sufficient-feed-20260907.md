# VBas 充足供料吞吐基准 Harness

## 关联变更

- OpenSpec：`benchmark-vbas-throughput-with-sufficient-feed`
- 目标主机：`192.168.29.11`
- 业务边界：不改变 A 服务合同、VBas 师生接口或视觉聚合规则

## 已实现能力

- 固定 fixture manifest：相对路径、字节数、分辨率、类别和 SHA-256。
- 按实例独立并发窗口持续补位，支持教师、学生和确定性 50%/50% 混合负载。
- 记录 batch/s、frame/s、P50/P95/P99、结果分类、在途比例和补位延迟。
- 按 GPU 采集显存、利用率、功耗、进程显存、容器状态和重启次数。
- 显存评估区分预热平台与稳态持续增长。
- Vision 媒体执行器通过默认关闭的观察器记录 FFprobe/FFmpeg 排队和执行阶段。
- 纯媒体零延迟 sink 和预抽帧真实租约分发回放。
- write-once `campaign_id/case_id/attempt` 证据树和失败关闭规则。

## 已执行的本地门禁

2026-09-07 从工作区根目录执行：

```bash
PYTHONPATH="$PWD/algorithm-scheduling-platform:$PWD" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  algorithm-scheduling-platform/tests/vbas_benchmark \
  algorithm-scheduling-platform/tests/test_vbas_batch_client.py \
  algorithm-scheduling-platform/tests/test_vision_cache.py

PYTHONPATH="$PWD/vision_orchestrator_service:$PWD/algorithm-scheduling-platform:$PWD" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  vision_orchestrator_service/tests

PYTHONPATH="$PWD/algorithm-scheduling-platform:$PWD" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  algorithm-scheduling-platform/tests/test_vbas_batch_client.py \
  algorithm-scheduling-platform/tests/test_vision_cache.py \
  algorithm-scheduling-platform/tests/test_vision_kafka_boundary.py

algorithm-scheduling-platform/.venv/bin/ruff check \
  algorithm-scheduling-platform/scripts/vbas_benchmark \
  algorithm-scheduling-platform/scripts/run_vbas_throughput_benchmark.py \
  algorithm-scheduling-platform/tests/vbas_benchmark \
  vision_orchestrator_service/app/infrastructure/media.py \
  vision_orchestrator_service/app/infrastructure/vbas.py \
  vision_orchestrator_service/app/infrastructure/capacity.py

PYTHONPATH="$PWD/algorithm-scheduling-platform:$PWD" MYPYPATH="$PWD" \
  algorithm-scheduling-platform/.venv/bin/mypy --strict --explicit-package-bases \
  algorithm-scheduling-platform/scripts/vbas_benchmark \
  algorithm-scheduling-platform/scripts/run_vbas_throughput_benchmark.py

python -m compileall -q \
  algorithm-scheduling-platform/scripts/vbas_benchmark \
  algorithm-scheduling-platform/tests/vbas_benchmark \
  vision_orchestrator_service/app

bash -n algorithm-scheduling-platform/deploy/scripts/run-vbas-throughput-staircase
```

当前已完整执行的 pytest 结果分别为 benchmark 相关 `41 passed`、Vision 服务
`98 passed`、平台视觉边界 `25 passed`；Ruff、strict Mypy、compileall 和 Bash
语法门禁通过。该结果只证明本地静态/单元和仿真边界，不代表三卡性能、真实媒体供料或完整链路通过。

## 待执行证据

- [ ] 三卡硬件、镜像 revision、有效配置、fixture 和显存基线预检。
- [ ] 教师单实例、学生单实例高位到低位阶梯。
- [ ] 教师三实例、学生三实例、50%/50% 混合三实例阶梯。
- [ ] 候选 95% 吞吐拐点两轮重复。
- [ ] 纯媒体 `16 -> 12 -> 8 -> 6 -> 4 -> 2 -> 1` 曲线。
- [ ] 预抽帧真实租约与分发回放。
- [ ] `C_vbas/C_feed/C_dispatch` 对比与空档归因。
- [ ] D0 与候选参数的完整视觉链路回验。
- [ ] 任务成功率、Consumer 健康、租约归零、临时媒体清理、容器零重启和显存长稳。

上述任一用例如被中断，必须保留为 `failed` 或 `interrupted`，并使用新 attempt 重跑。

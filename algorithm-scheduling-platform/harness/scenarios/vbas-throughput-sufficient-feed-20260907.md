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
`99 passed`、平台视觉边界 `25 passed`；新增完整链路分析器后，基准工具全量为
`33 passed`；最终组合回归为 `58 passed`。Ruff、strict Mypy、compileall 和 Bash
语法门禁通过。该结果只证明本地静态/单元和仿真边界，不代表三卡性能、真实媒体供料或完整链路通过。

## 远端身份与证据根

- Campaign：`vbas-throughput-20260907-d387d7f`。
- 证据根：`/data/result/_harness/vbas-throughput/vbas-throughput-20260907-d387d7f`。
- Fixture manifest SHA-256：`81c16dca90e052ddf954c70ef08b9d783b47c4bcbbd1f444b83e9d1251c75422`。
- GPU：GPU0/1 为 RTX 4090 D，GPU2 为 RTX 3090，三卡总显存均约 24 GiB。
- VBas 镜像：`sha256:c3261f111088249c387e5cc2ed47ac781c136fbac5dd139aae8339cfe1062c68`，
  revision `61b5fdc73f254e6416fc985cec4a7ebee799ae7b`。
- Vision 镜像：`sha256:739becaede8893a92c6573b4255a3604c1a1c73667bc9926ef23da20dea5d682`，
  revision `e3ed85a90516f2ab834a1b2adecd32de7b36059d`。

## VBas 裸能力与拐点

三实例持续供料结果如下，吞吐单位均为 batch/s：

| 模式 | 最高吞吐档 | 最高吞吐 | 95% 最低并发拐点 | 两轮候选吞吐 |
| --- | ---: | ---: | ---: | --- |
| 教师 | c2 | 15.117 | c2 | 15.117 / 14.808 |
| 学生 | c4 | 2.814 | c2 | 2.715 / 2.672 |
| 50%/50% 混合 | c8 | 4.792 | c3 | 4.666 / 4.641 |

候选两轮偏差均不超过 5%。混合 c8 虽有一次完整成功窗口，但此前 attempt 出现低频 VBas
`AttributeError` HTTP 500，因此 c8 只作为最高观测吞吐保留，不作为可靠生产建议；混合推荐 c3。

## 媒体供料与真实分发

纯媒体活动流从 `16 -> 12 -> 8 -> 6 -> 4 -> 2 -> 1` 完整降阶：

| 流 | 最高吞吐 | 95% 最低拐点 | s16 吞吐 | 结论 |
| --- | ---: | ---: | ---: | --- |
| 教师 | s4 = 2.494 | s4 | 1.480 | s16 过度调度 |
| 学生 | s6 = 2.208 | s4 = 2.152 | 1.428 | s4 已达峰值 97.5% |

一个 FFmpeg 进程约创建 161 个线程；s16 峰值约 2576 个线程，load average 超过 116，
iowait 很低。因此高位下降不是磁盘瓶颈，而是 CPU/线程争用。全部媒体隔离 attempt 的临时目录已清理。

预抽帧真实租约分发结果：

| 流 | 成功 | `C_dispatch` | 租约等待 P95 | 分配 |
| --- | ---: | ---: | ---: | --- |
| 教师 c2 | 1000/1000 | 14.034 | 0.018s | 342/327/331 |
| 学生 c2 | 1000/1000 | 2.274 | 1.653s | 396/388/216 |

学生分配差异来自两张 4090 D 与一张 3090 的服务能力差异；所有实例持续参与且无饥饿，
不能按异构卡的 batch 绝对均分判失败。学生首次 attempt 因宿主机无法解析 Compose 内部
`vbas-gpu*` 地址失败，原失败证据保留；第二次在 `algorithm-platform` 网络内完整通过。

## 三段能力对比

推荐拐点两轮均值用于 `C_vbas`：

| 流 | `C_vbas` | `C_dispatch` | `C_feed` | 供料相对裸能力 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| 教师 | 14.962 | 14.034 | 2.494 | 16.7% | 明确受媒体/扫描供料限制 |
| 学生 | 2.694 | 2.274 | 2.152 | 79.9% | 媒体略低于分发和裸能力 |

教师分发达到裸能力约 93.8%，但媒体只达到约 16.7%；学生分发约为裸能力 84.4%，媒体约为
79.9%。这证明 Control 租约和 HTTP 分发不是完整链路主瓶颈，教师自适应扫描和 FFmpeg 供料
才是主要缺口。

## 完整链路回验

固定同一教师/学生视频，每轮 8 个全新课程 task ID、16 个视觉节点：

| 配置与 attempt | 墙钟 | 成功 batch | 链路 batch/s | 平均活跃槽位 | 三实例同时空闲 | 峰值槽位 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| D0 `offline=1/media=8` attempt-002 | 1148s | 1328 | 1.181 | 0.655/3 | 51.34% | 3/3 |
| 候选 `offline=2/media=4` attempt-001 | 672s | 1328 | 2.013 | 1.142/6 | 28.59% | 5/6 |
| 候选 `offline=2/media=4` attempt-002 | 675s | 1328 | 2.025 | 1.130/6 | 26.43% | 5/6 |

候选平均链路吞吐为 2.019 batch/s，比 D0 提升 70.87%；平均墙钟 673.5 秒，比 D0 缩短
41.33%。候选两轮吞吐偏差为 0.603%，墙钟偏差为 0.45%。两张同型号 4090 D 的成功 batch
差异分别为 3.96% 和 0.66%，均低于 10%；3090 按较长服务时间和异构能力评估。

两轮候选均没有达到“同时空闲低于 10%、平均活跃不低于 4.8/6、出现六槽并发”的完整链路
供满目标：峰值均只有 5/6，六槽时间为 0。租约/分发空档为 0，租约等待 P95 为 44–50ms，
空档均归因于 ready batch 不足。候选是明确的效率改进，但不得标记为“六槽持续供满通过”。

## 成功、显存与清理门禁

- D0 attempt-002 和两轮候选均为 16/16 节点状态 60、Consumer ready、三实例租约归零、
  临时课程媒体目录为空、Vision/VBas 容器重启计数为 0。
- D0 三卡各约 1037 个一秒级样本，候选每轮各约 611 个样本；全部形成稳定显存平台。
- 候选第一轮平台约为 13.7/13.5/14.6 GiB；第二轮约为 13.4/13.5/13.9 GiB。
  第二轮 GPU2 最近窗口斜率约 52.8 MiB/分钟，低于 64 MiB/分钟门槛，且前后半段 P50
  没有继续抬升。
- D0 有一个教师 batch 首次发生瞬时传输故障，租约释放后同 batch 重试成功；最终为 1328 个
  成功 batch、1329 次 HTTP attempt。分析器按 `lease_released` 关闭失败 attempt，避免误报容量越界。
- D0 attempt-001 的首次终态采集误用未补零编号，原 404 文件和修正说明均保留；其 Vision
  日志又因采集截止过早缺少最后一个 finish，故不用于最终 D0 通过结论。attempt-002 是权威 D0。

## 最终运行状态与结论

`192.168.29.11` 当前保留实测候选：VBas 每实例 `MaxConcurrentOfflineBatches = 2`、Vision
`media.max_concurrent_processes = 4`、`worker.concurrency = 16`。测试结束后
`benchmark.stage_logging_enabled` 已恢复为 `false`；Vision 当前容器 healthy、restart 0、
revision 为 `e3ed85a`，三台 VBas 均 healthy、离线租约为 0。

该参数建议只适用于上述三卡异构硬件、指定镜像、8 路教师/学生完整视觉负载。`media=4` 有纯媒体
曲线和两轮完整链路支撑；`offline=2` 有裸能力和显存稳定性支撑，但完整链路尚不能持续填满六槽，
不应继续提高容量来掩盖供料不足。后续性能优化应聚焦教师自适应扫描的帧预取/解码复用和显式
ready queue，而不是扩大 VBas 容量或 Control 重试。

本次 change 的基准执行、失败保留、归因和参数收敛工作已完成；A 服务、VBas HTTP 路径和响应、
视觉聚合语义均未改变。完整链路“六槽持续供满”性能验收明确未达到，这是最终测试结论而非遗漏用例。

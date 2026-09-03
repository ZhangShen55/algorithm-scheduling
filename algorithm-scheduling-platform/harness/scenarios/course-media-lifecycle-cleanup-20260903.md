# 课程媒体终态清理与视觉失败事件发布验证（2026-09-03）

## 目标与边界

- 修复课程临时媒体只下载、不按实际消费者终态释放的问题。
- 修复视觉节点失败已入库但未发布 `VISUAL_ANALYSIS_FAILED` 终态事件的问题。
- 只发布受影响的 `orchestrator-service` 与 `vision-orchestrator-service`；本次不构建、
  不替换 `control-service`，也不修改 A 服务 HTTP 契约。
- `/data/course/{task_id}` 是临时工作区；`/data/result/{task_id}` 是持久结果目录，清理不得越界。

## 实现事实

- `PPT_SLICE` 终态后释放 `slides.mp4`，不影响已经写入 `/data/result/{task_id}/ppt` 的切片结果。
- ASR 终态后释放抽取的 `teacher.wav`。
- 同一 `submission_id` 下 ASR 与教师行为的实际消费者全部终态后释放 `teacher.mp4`。
- 学生行为终态后释放 `student.mp4`。
- 教师或学生视觉任务终态后释放对应的 `/data/course/{task_id}/vision/t|s` 工作目录。
- 全部已请求任务类型终态后清理 `/data/course/{task_id}`；同时增加默认每 60 秒执行一次的
  终态目录对账，以处理进程中断后遗留的可清理目录。
- Vision 在失败状态持久化后发布 `VISUAL_ANALYSIS_FAILED`。若事件发布失败，Kafka 命令不确认；
  重放时补发失败终态事件，不重复执行视觉推理。

## 本地验证

目标 Git 提交：`f3329c61b775235421d93dae7dc1d44518e5b180`。

- `orchestrator_service`：`98 passed`。
- Orchestrator 结构测试：`6 passed`。
- `vision_orchestrator_service`：`66 passed`。
- 聚焦媒体清理与失败事件测试：`76 passed`。
- 平台相关选择测试：`7 passed, 1 deselected`。
- 两个服务的 `compileall`、选择范围 Ruff 和 `git diff --check` 通过。
- 平台其他失败来自并行工作树中的 `capacity_pool` 旧断言，未计入本变更结果。

## 远端构建

目标服务器：`192.168.29.11`，架构 `amd64`。构建来源为目标提交的干净 Git archive：
`/root/workspace/algorithm-scheduling-release-f3329c6`，没有从服务器脏工作区构建。

| 服务 | 新镜像 ID | OCI revision | 校验 |
| --- | --- | --- | --- |
| `orchestrator-service` | `sha256:f730c4e6946e1048203578a5fe4829c274faa38a120f97ee9b82042678db27e5` | `f3329c61b775235421d93dae7dc1d44518e5b180` | `amd64`、compile/import、源码 manifest 通过 |
| `vision-orchestrator-service` | `sha256:8e87007f800fa7fb82f3a91e353ad736cba8f8b339db3b7e9a61fee1193f5b06` | `f3329c61b775235421d93dae7dc1d44518e5b180` | `amd64`、compile/import、源码 manifest 通过 |

构建未使用 `--no-cache`，未执行 `docker buildx prune` 或宽泛 Docker 清理。发布后 BuildKit
缓存仍存在，大小约 `93.81 GB`。临时 Git archive 目录在镜像和运行门禁通过后精确删除。

## 远端替换与运行门禁

Compose 只对两个目标服务执行 `--no-deps --force-recreate`。Orchestrator 继续使用服务器现有
`/root/workspace/algorithm-scheduling/orchestrator_service/config.toml`；Vision 继续使用原运行
配置 `/root/workspace/runtime-config/0c6186c/vision-orchestrator-service/config.toml`，没有回退租约
续期、容量快照和瞬时故障退避参数。

| 服务 | 新容器完整 ID | 状态 | 重启次数 |
| --- | --- | --- | --- |
| `orchestrator-service` | `875c0ac2898469a06c219a4bd308206d7638d395e06bfe85406e89f64b99b140` | `running/healthy` | `0` |
| `vision-orchestrator-service` | `ca042b4d819017f06c05d1b4d2b683ca7c1daf0943e14f484966a043bea8355a` | `running/healthy` | `0` |

- Orchestrator `/ops/readiness` 返回 HTTP 200；Outbox Publisher、课程 Consumer、节点执行器、
  视觉分发/事件 Consumer、PPT 对账、PostgreSQL、Kafka 和 Control 检查均为 ready。
- Vision `/ready` 返回 HTTP 200；视觉命令 Consumer、PostgreSQL、Kafka 和 Control 均为 ready。
- 两服务启动日志没有 Traceback、Consumer unhealthy 或业务 ERROR。
- 本次操作前后 `control-service` 容器均为
  `52bca0eb13a389cd9b02ac319f3d8db6b8312bce13f39a81de0cbcbe87ad6683`，镜像均为
  `sha256:25497bcf1eb2de95fd31f61fcdae2c008eaa959fe2de13f1b5087db8b3770c61`，状态始终为
  `running/healthy`、重启次数 `0`。其 revision `73e194a809f9c3d1d460e2b7ee6550ced79420c0`
  是本次开始前已有部署，本次没有构建或替换该服务。

## 清理证据

- 周期终态对账自动删除历史目录
  `/data/course/mixed-16full-300x30000-20260902-172431-full-06`，该目录删除前约 `975 MiB`。
- `/data/course` 从约 `1.5 GiB` 降为约 `466 MiB`，只剩受保护的 `_harness` 目录。
- `/data/result` 保持约 `7.1 GiB`，目录仍存在且未执行删除。
- Compose 替换产生的旧容器
  `f902f31b062a35f296d4a5bf2e13beedaf9cdafdfc53d92c6ea0fea42e52e509` 和
  `d0aa0e65163e850eb41c45197527abb917f44768a98483737153452e4f333beb` 已不存在。
- 无容器引用后，精确删除旧镜像
  `sha256:2b0c00d030be9a91d709fb3136b9fbbd436aef98053d1ef930d86b1d0737442e` 和
  `sha256:a51018ad8c02d6d906e421d8e0fc7983b3abc632e1912e67976e9bbed3284d41`。
- 未删除其他镜像、运行容器、Docker volume、BuildKit 缓存或 `_harness` 证据。

## 结论

本变更达到静态、单元和真实远端服务运行验证层级。课程临时媒体已能按共享消费者终态释放，
视觉失败终态具备可靠补发路径，周期对账成功清理了真实历史终态残留；持久结果和 Harness 边界
保持不变。尚未执行新的完整课程业务推理，本记录不将发布门禁扩大为新的端到端算法精度结论。

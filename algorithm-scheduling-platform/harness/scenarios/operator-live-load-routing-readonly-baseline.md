# 公共算子实时负载路由只读基线

## 记录目的

本记录属于 `balance-operator-routing-by-live-load` 的修复前调查证据。调查目标是确认
VBas 的首实例独占是否也影响 ASR、PPT Slice 和 OCR。它不是新版本通过证据，也不得替代
发布后的 20 路离线、1000 路在线或混合负载验收。

## 环境与边界

- 调查时间：2026-08-27。
- 目标服务器：`192.168.29.11`。
- 目标 Git revision：`d449dbad6b5744a936a3b73cc3bde3b35442e0b8`。
- 只通过既有北向 `POST /api/course-jobs` 创建测试任务。
- 未调用 drain、lifecycle、unregister 等管理接口。
- 未修改代码、配置、容器、镜像或数据库结构。
- `nvidia-smi` 只用于辅助观察；结论以 Control 活跃租约和算子真实请求日志为准。

## 北向任务类型负例

`PPT_OCR` 不是北向 `task_types`，而是 `PPT` DAG 内部节点。任务
`investigation-balance-20260827-invalid-ppt-ocr` 返回 HTTP 200、业务码 `40001`，原因是
`body.task_types.0` 校验失败。合法北向类型仍为 `PPT`、`ASR`、`TEACHER_BEHAVIOR` 和
`STUDENT_BEHAVIOR`。

## ASR 十六路

任务 ID 为 `investigation-balance-20260827-asr-170743-01` 至
`investigation-balance-20260827-asr-170743-16`。

- 受理：16/16，HTTP 200、业务码 0。
- 调查结束时：15 个 `ASR_TRANSCRIPTION=60`，1 个为 50。
- 多轮 Control 活跃租约采样：GPU0 最多同时持有 4 个，GPU1/GPU2 始终为 0。
- `/v1.1.8/seacraft_asr` 真实调用：`asr-offline-gpu0=16`、`gpu1=0`、`gpu2=0`。

高负载期间 `asr-offline-gpu0` 心跳曾出现约 24 秒间隔，超过 TTL 后被 Control 瞬时判为
`OFFLINE`；容器始终 `running/healthy`、重启次数为 0，后续心跳恢复并自动回到 `ONLINE`。
这说明首实例集中负载同时放大了心跳延迟风险。

## PPT 与 OCR 十六路

任务 ID 为 `investigation-balance-20260827-ppt-171340-01` 至
`investigation-balance-20260827-ppt-171340-16`。

- 受理：16/16，HTTP 200、业务码 0。
- 调查结束时：4 个 `PPT_SLICE=60/PPT_OCR=60`；1 个 `60/10`；3 个 `50/20`；
  8 个 `10/20`。
- PPT Slice 真实调用：`ppt-slice-cpu0=8`、`cpu1=0`、`cpu2=0`。
- OCR 活跃采样曾显示 GPU0 同时持有 4 个 OCR 租约。
- `/ocr/prediction` 真实调用：`ocr-gpu0=72`、`gpu1=0`、`gpu2=0`。

## 结论

ASR、PPT Slice、OCR 与 VBas 具有相同的首实例优先问题。根因位于公共 Redis 租约选择器，
不是某个算子独有的批处理逻辑。`balance-operator-routing-by-live-load` 对公共选择器的修复会
覆盖七类已注册算子；发布后仍须分别执行公共租约回归和 VBas 三场景真实验收，不能直接把
本基线换名为通过证据。

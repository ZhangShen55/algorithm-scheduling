# VBas 请求内顺序推理验证记录

## 范围与边界

本记录对应 OpenSpec 变更 `serialize-vbas-intra-request-inference`。变更只控制单个学生请求内
人数、人脸、学生行为三个模型的执行顺序，以及单个 `/AE/SyncTasks2` 请求内 Polygon 的执行
顺序；不增加 `GpuInferenceConcurrency`，不限制不同 HTTP 请求之间的并发，也不调整在线、
离线容量参数。

## 变更前基线

- 接口合同：`POST /ImageDetect/student/v1.0.0`、
  `POST /ImageDetect/teacher/v1.0.0`、`POST /AE/SyncTasks2`。
- 学生接口原先在一个请求内通过三个 `asyncio.to_thread` 和 `asyncio.gather` 同时执行人数、
  人脸和学生行为模型。
- `SyncTasks2` 原先为一个请求中的多个 Polygon 创建并发协程列表。
- 2026-08-31 变更前远端历史观测：GPU0 的 VBas 进程曾驻留约 `15028 MiB`；重启后约
  `1064 MiB`，单次人数请求后约 `2132 MiB`，单次学生行为请求后约 `4954 MiB`。
- 历史 CUDA OOM 日志显示 PyTorch allocated 约 `12.5 GiB`、reserved but unallocated 约
  `0.75 GiB`，VBas 进程约 `14.68 GiB`。该历史值是问题基线，不代表本次发布结果。

本记录不保存 Base64、完整图片内容、凭据或完整请求/响应，只保留可复核的合同、状态、结果
摘要和资源数字。

## 本地自动化验证

环境：macOS，Conda `jy-tias`，Python 3.11，VBas 使用 CPU 配置。

```bash
conda run -n jy-tias python -m compileall -q app scripts tests
conda run -n jy-tias python -m pytest -q tests
conda run -n jy-tias python -m pip check
openspec validate serialize-vbas-intra-request-inference --strict
```

结果：VBas 全量测试 `90 passed, 4 warnings`，编译通过，`pip check` 返回无损坏依赖，
OpenSpec 严格校验通过。4 条警告均为 FastAPI `on_event` 弃用提示，与本变更行为无关。

自动化测试覆盖：

- `[Inference]` 缺省值、显式值和缺字段回退；
- 学生三模型顺序路径和并行兼容路径；
- `StudentModelsSequential` 对正式分析入口的分支选择；
- Polygon 顺序/并行执行以及输出顺序；
- Person、Face、Student、Teacher 四个 `UseHalf` 的独立映射；
- GPU 推理完成后的 PyTorch allocated/reserved 及进程生命周期峰值日志；
- 文件路径兼容实现和 Base64 正式实现的人数、人脸精度映射；
- 原接口路径和平台注册元数据不变。

## 本地真实推理

使用 `app.main:app`、单 Uvicorn worker 在 `127.0.0.1:18981` 启动。`/AE/Health` 返回
`status=ok`、`model_ready=true`，OpenAPI 包含三个正式推理接口及 `/ops/metadata`、
`/ops/status`。固定图片真实推理结果如下：

| 接口 | 图片 | 结果摘要 |
| --- | --- | --- |
| 学生行为 | `tests/student_behavior_eval/filtered_by_config/frame_000103.jpg` | 成功；人数 30、人脸 7，并返回既有五类学生行为项 |
| 教师行为 | `tests/teacher_behavior_drawn/images/00000068-uuwa.jpg` | 成功；返回既有站、坐、板书、讲授结果项 |
| `SyncTasks2` 单 Polygon | `tests/teacher_person_count/frame_000068.jpg` | 成功；`full` 人数 30、人脸 9 |
| `SyncTasks2` 双 Polygon | 同上 | 成功；按输入顺序返回 `left`、`right`，人数分别 14、16 |

四次真实请求结束后 `/AE/WorkerStatus` 显示成功 4、失败 0、运行中离线/在线请求均为 0、
在线队列为 0。`/ops/metadata` 的能力仍为 `student_behavior`、`teacher_behavior` 和
`person_count`，`/ops/status` 的声明容量仍为 1024。

## 远端三卡发布与验证

目标机：`192.168.29.11`。本节将在同一 Git SHA 构建、逐实例替换及三卡真实 GPU 验证后
补充完整容器/镜像 ID、注册、GPU 绑定、P50/P95、显存峰值/驻留值和清理结果。在全部门禁
通过前保留旧 VBas 镜像，不将本变更标记为发布完成。

## 剩余风险

本阶段只消除一个请求内部的推理扇出。不同 HTTP 请求仍可并发进入同一 VBas 实例；若远端
固定并发回归后显存仍异常增长或出现 OOM，应单独设计 `GpuInferenceConcurrency`，不能在本
变更中临时追加未验证的进程级限流。

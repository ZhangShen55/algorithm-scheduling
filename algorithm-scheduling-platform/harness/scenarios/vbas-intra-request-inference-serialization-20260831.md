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

结果：VBas 全量测试 `92 passed, 4 warnings`，编译通过，`pip check` 返回无损坏依赖，
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

目标机：`192.168.29.11`，架构 `amd64`。最终候选 Git SHA 为
`61b5fdc73f254e6416fc985cec4a7ebee799ae7b`，镜像为
`algorithm-vbas:v1.0_260831`，完整镜像 ID 为
`sha256:c3261f111088249c387e5cc2ed47ac781c136fbac5dd139aae8339cfe1062c68`。
镜像 revision 与候选 SHA 一致，容器内编译及 `pip check` 通过，四个模型文件齐全。

构建复用了既有 BuildKit layer cache，未使用 `--no-cache`，未执行 builder、buildx 或 system
prune。构建前缓存约 87.22 GiB，完成与精确清理后缓存约 90.02 GiB，证明缓存被保留。

### 替换前账本

替换前镜像为 `algorithm-vbas:v1.0_260829`，完整 ID
`sha256:96b69779de8db972d4e011720c95e970ce1afb70778c00c60a100b4d30833f7f`。
三个旧容器完整 ID分别为：

- GPU0：`ae662185c200cc031a6fec5eb482b99e947d451c77647fe867d486db13cba3bf`；
- GPU1：`67958d201da5f31be57605fc910fa2c2f5bbac1e432dcaa0ec4db12e4f5fcf30`；
- GPU2：`172486527cc137fc1c86acddbc8a09b03c19b227e7d1a78835b3bb84bd4a7057`。

替换前全部 healthy，分别绑定 GPU 0、1、2；Control 快照显示三实例均 `ONLINE`、
`model_ready=true`、`reported_inflight=0`、`active_lease_count=0`。有效容量为平台 1024、
离线 1、在线 24、在线队列 24。

### 受控替换结果

三个实例按 GPU0、GPU1、GPU2 顺序逐个重建，每个实例通过 healthy、单 worker、GPU 绑定、
注册和配置检查后才继续下一个。最终资产如下：

| 实例 | 完整容器 ID | GPU | 状态 |
| --- | --- | ---: | --- |
| `vbas-gpu0` | `80da3c31d10209514ba5b2a5d8fb8592af6b18b3cccc5961be9c9e9fce68cbe7` | 0 | healthy / ONLINE |
| `vbas-gpu1` | `f84e8644b2dfe6f7e96ad034f6d06fe79dcd8e4884dcb53403a516e06664d323` | 1 | healthy / ONLINE |
| `vbas-gpu2` | `eaeff507ade6b70d6e5dc05edfd193a59ace0e8340e364926d848b2d7413d4f5` | 2 | healthy / ONLINE |

每个容器只有一个 `vbas -m uvicorn app.main:app ... --workers 1` 进程。容器内六项
`[Inference]` 均与发布配置一致，两个顺序开关为 `true`，四个精度开关为 `false`；未出现
`GpuInferenceConcurrency`，既有容量没有变化。三个实例继续注册
`student_behavior`、`teacher_behavior` 和 `person_count`。

### 预热与显存基线

重启后三实例依次执行学生、教师和人数预热，全部 HTTP 200：

| GPU | VBas PID | 进程驻留显存 | allocated | reserved | max allocated | max reserved |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 3124889 | 3192 MiB | 768.62 MiB | 2716 MiB | 1959.41 MiB | 2716 MiB |
| 1 | 3128310 | 3192 MiB | 768.62 MiB | 2716 MiB | 1959.41 MiB | 2716 MiB |
| 2 | 3131960 | 3804 MiB | 768.62 MiB | 3434 MiB | 2217.50 MiB | 3434 MiB |

GPU2 为 RTX 3090，GPU0/1 为 RTX 4090 D，因此 allocator reserved 和进程驻留值存在差异。
后续重复推理、混合并发及 72 路网关请求结束后，上述 VBas 进程驻留值和 allocator 峰值均
未继续增长，三个新容器日志中 OOM、CUDA OOM 和 Traceback 计数均为 0。该结果显著低于
变更前约 15 GiB 历史高水位，但不代表跨 HTTP 请求并发已被全局限制。

### 三实例固定图片结果与延迟

每个实例对学生、教师、单 Polygon 和双 Polygon 分别执行 5 次真实推理，60/60 成功：

| GPU | 操作 | P50 | P95 | 固定结果摘要 |
| ---: | --- | ---: | ---: | --- |
| 0 | 学生 | 0.1366 s | 0.1480 s | 人数 30、人脸 7、行为计数稳定 |
| 0 | 教师 | 0.0291 s | 0.0326 s | 主体 1、坐 1、板书 1 |
| 0 | 单 Polygon | 0.1003 s | 0.1023 s | `full` 人数 30 |
| 0 | 双 Polygon | 0.1196 s | 0.1547 s | `left/right` 人数 14/16 |
| 1 | 学生 | 0.1316 s | 0.1782 s | 人数 30、人脸 7、行为计数稳定 |
| 1 | 教师 | 0.0322 s | 0.0339 s | 主体 1、坐 1、板书 1 |
| 1 | 单 Polygon | 0.1001 s | 0.1021 s | `full` 人数 30 |
| 1 | 双 Polygon | 0.1242 s | 0.1563 s | `left/right` 人数 14/16 |
| 2 | 学生 | 0.2189 s | 0.2667 s | 人数 30、人脸 7、行为计数稳定 |
| 2 | 教师 | 0.0291 s | 0.0314 s | 主体 1、坐 1、板书 1 |
| 2 | 单 Polygon | 0.1562 s | 0.1571 s | `full` 人数 30 |
| 2 | 双 Polygon | 0.1820 s | 0.2170 s | `left/right` 人数 14/16 |

所有双 Polygon 响应都保持输入顺序 `left`、`right`。单次预热延迟不计入上述分位数。

### 混合并发与网关路由

- 三实例同时执行一条离线学生或教师请求和一条在线人数请求，共 6 轮、36 个请求，
  `36/36` 成功，三实例各完成 12 个；P50 0.1480 秒，P95 0.3655 秒。
- 通过 Online Gateway `/online/vbas/person-count` 同时提交 72 个单图请求，`72/72` 成功，
  P50 1.8391 秒，P95 3.3286 秒，最大 3.8761 秒。
- 三实例成功计数增量分别为 GPU0=25、GPU1=24、GPU2=23，证明平台注册与动态路由没有
  回归；测试后 `reported_inflight`、在线/离线池和 `active_lease_count` 全部归零。

### 逐模型 FP16 有效性

使用 GPU0 临时容器分别只开启 `PersonUseHalf`、`FaceUseHalf`、`StudentUseHalf` 和
`TeacherUseHalf`。每轮核对只有目标字段为 `true`，随后执行学生、教师、单/双 Polygon：

- 四轮共 16 个请求全部 HTTP 200；
- 人数仍为 30，双 Polygon 仍为 14/16；
- 学生结果计数仍为 `[30, 7, 1, 0, 1, 0, 1]`；
- 教师结果计数仍为 `[1, 0, 1, 1, 0]`；
- 临时容器验证后均已移除，生产三实例四个 `UseHalf` 最终保持 `false`。

### 旧资产清理与清理后 Smoke

全部门禁通过后，按完整 ID精确删除 `v1.0_260829`、`v1.0_260827`、`v1.0_260826`、
`v1.0_260825` 四个旧 VBas 镜像和中间候选镜像
`sha256:6db515fb37d94f1a8d36133a4bece4517c2f60c00be754524ebf8f6234db5bed`。
未删除其他算子镜像、平台镜像或构建缓存。

清理后，三个最终容器再次分别执行学生、教师、单/双 Polygon，共 12 个请求全部成功；
三个容器仍 healthy，Control 中仍为 ONLINE、model ready、租约归零，GPU 绑定和完整容器 ID
未变化。远端发布门禁通过。

## 剩余风险

本阶段只消除一个请求内部的推理扇出。不同 HTTP 请求仍可并发进入同一 VBas 实例；若远端
固定并发回归后显存仍异常增长或出现 OOM，应单独设计 `GpuInferenceConcurrency`，不能在本
变更中临时追加未验证的进程级限流。

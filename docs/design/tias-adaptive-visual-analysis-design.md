# VBas 自适应视觉分析与适配调度设计

> 文档版本：1.0  
> 形成日期：2026-07-28  
> 状态：方向设计稿  
> 关联项目：视觉编排与聚合位于 `/Users/zhangshen/Documents/workspace/jy-vision-orchestrator-server`（`git@github.com:ZhangShen55/jy-vision-orchestrator-server.git`）；帧级推理位于当前工作区 `vbas`。

## 1. 文档目的

本文档单独描述课后 T/S 视频视觉分析方向，重点解决以下问题：

- 将视觉分析编排与结果聚合从 TIAS 实例选择、协议转换中分离。
- 复用多个 TIAS Docker 实例，提高 GPU 推理吞吐。
- 允许离线图片按可配置批次和有限并发调用 TIAS。
- 对教师板书和坐姿采用可配置的粗扫与多轮加密检测，得到接近真实的行为起止区间。
- 对单帧误检、短暂检测缺口、重复候选窗口和边界误差进行容错。

本设计的课程级 Worker、RemoteFrameAnalyzer 和实例调度逻辑已抽离至 `jy-vision-orchestrator-server`；`vbas` 仅保留帧级模型推理和实例运行状态。

## 2. 当前实现观察

当前外部 `jy-vision-orchestrator-server` 的视觉分析 Worker 承担以下职责：

```text
下载 T/S 视频
→ 固定间隔抽帧
→ 调用 RemoteFrameAnalyzer
→ RemoteFrameAnalyzer 选择 TIAS 实例并请求
→ 聚合帧指标
→ 生成快照、时间线、行为统计和评分
→ 写入业务数据库
```

现有 `frame_interval_seconds` 默认固定为 30 秒，能够生成全课稀疏样本，但不能根据检测结果自动缩小抽帧间隔，也不能进行多轮行为边界搜索。

现有实现已经具有可复用基础：

- TIAS 实例注册和 Redis TTL。
- `TiasScheduler` 的能力匹配、容量判断、熔断和实例选择。
- `RemoteFrameAnalyzer` 的批次请求、失败重试和结果回填。
- T/S 视频抽帧、快照、行为统计和数据库 Repository。

目标不是推倒重来，而是强化服务边界并增加自适应时间分析策略。

## 3. 目标服务边界

### 3.1 视觉分析编排与聚合服务

由外部 `jy-vision-orchestrator-server` 负责：

- 消费离线视觉节点任务。
- 读取 `/data/course/{course_job_id}/source/T.mp4` 和 `S.mp4`。
- 维护粗扫和加密检测策略。
- 根据时间戳抽取、缓存和去重图片帧。
- 将帧按 stream、capability 和批次大小分组。
- 调用 TIAS 适配路由服务。
- 将 TIAS 原始输出标准化为帧观察结果。
- 识别候选窗口、细化事件边界、合并短缺口。
- 计算课程时间线、行为时长、比例、快照和评分。
- 将结构化结果写入现有视觉业务库。
- 将需要长期展示的快照发布到 `/data/result/{course_job_id}/vision/snapshots`。

该服务拥有“为什么还要再抽一批帧”的决策权。

### 3.2 TIAS 适配与路由服务

作为外部视觉编排服务内的独立调度职责，负责：

- 接收在线单图请求或离线图片批次。
- 按 capability 查询已注册 TIAS 实例。
- 校验请求图片数量不超过实例 `max_batch_size`。
- 原子占用实例并发容量。
- 将平台请求转换为 VBas 学生/教师推理协议：`/ImageDetect/student/v1.0.0` 或 `/ImageDetect/teacher/v1.0.0`。
- 将一次完整请求绑定到一个 TIAS 实例。
- 执行超时、有限重试、熔断和容量释放。
- 保持 frame_id、timestamp 与响应项的对应关系。
- 返回原始或标准化帧级结果。

该服务不负责视频抽帧、不决定下一轮扫描范围、不聚合课程行为区间、不写视觉业务库。

### 3.3 TIAS 实例池

TIAS Docker 实例负责模型推理。每个实例主动注册：

```text
instance_id
service_code = "tias"
capabilities = [student_behavior, teacher_behavior, teacher_head_pose]
service_url
model_version
max_concurrency
max_batch_size
running_batches
queued_batches
gpu_id
status
last_heartbeat
```

## 4. 总体调用关系

```text
                  离线视觉任务
                        │
                        ▼
              视觉分析编排与聚合服务
                 │                  ▲
       生成抽帧计划                  │ 帧级结果
                 ▼                  │
             图片批次 ─────→ TIAS 适配与路由服务
                                     │
                          注册查询 + 容量占位
                                     │
                         ┌───────────┼───────────┐
                         ▼           ▼           ▼
                     TIAS 实例 A  TIAS 实例 B  TIAS 实例 C

在线 Base64 图片 ─→ 在线网关 ─→ 同一个 TIAS 适配与路由服务
```

在线和离线共享实例注册与容量路由，但调用约束不同：

| 场景 | 输入 | 调度单位 | 是否批次 | 是否聚合 |
|---|---|---|---|---|
| 在线 TIAS | Base64 图片 | 完整 HTTP 请求 | 通常单图 | 不聚合，直接同步返回 |
| 离线视觉 | 本地视频抽帧 | 一个图片批次 | 是，大小可配置 | 由视觉分析服务多轮聚合 |

## 5. 离线帧输入方式

调度平台和 TIAS 运行在同一台服务器。离线任务推荐优先传共享本地路径，而不是把图片反复编码为 Base64：

```text
/data/course/{course_job_id}/frames/teacher/{frame_id}.jpg
/data/course/{course_job_id}/frames/student/{frame_id}.jpg
```

所有参与离线视觉处理的容器使用一致的 `/data/course` 只读或读写挂载。适配器将本地路径映射到 TIAS `StoragePath`。如果某轮帧只存在于内存或后续迁移到多机部署，可以退化为 Base64 输入。

Kafka 消息不携带图片，只携带任务标识、视频路径和策略配置。

## 6. 教师行为语义

当前教师模型输出站、坐、板书、讲授。聚合层应将其拆成两个维度：

```text
姿态状态：STANDING / SITTING
活动标签：WRITING、TEACHING
```

- `STANDING` 和 `SITTING` 二选一；正常情况下必须存在一个姿态状态。
- `WRITING` 与 `TEACHING` 是独立活动标签，不与姿态互斥，也不强制彼此互斥。
- `suspected_sitting`、`posture_fallback` 和缺少有效主体不应直接生成最终坐姿区间，而应触发附近补帧或标记低可信结果。

建议标准化单帧输出：

```json
{
  "frame_id": "teacher-000123",
  "timestamp_ms": 1200000,
  "posture": "STANDING",
  "posture_confidence": 0.91,
  "writing": true,
  "writing_confidence": 0.88,
  "teaching": true,
  "teaching_confidence": 0.76,
  "suspected_sitting": false,
  "posture_fallback": false,
  "model_version": "teacher-behavior-v1"
}
```

视觉聚合服务第一版不再增加另一套 `positive_threshold` 和 `negative_threshold`。TIAS 已经完成标签阈值判断，聚合层直接使用标签结果；置信度主要用于审计、疑似点补帧和结果可信度计算。

## 7. 自适应扫描配置

推荐配置示例：

```toml
[visual_analysis]
coarse_interval_seconds = 30
refine_intervals_seconds = [10, 5, 2, 1]
tias_batch_size = 8
batch_concurrency = 2
max_refine_rounds = 4
max_detection_points_per_course = 5000

[visual_analysis.writing]
enabled = true
max_gap_seconds = 3

[visual_analysis.sitting]
enabled = true
max_gap_seconds = 5
```

约束：

- `coarse_interval_seconds` 可以调整为 10、15 或 30 秒。
- `refine_intervals_seconds` 必须严格递减，最后一级表示目标边界精度。
- `tias_batch_size` 不得超过可选 TIAS 实例声明的 `max_batch_size`。
- `batch_concurrency` 控制一个视觉任务同时提交的 TIAS 批次数。
- 一个批次不混合不同 stream 或 capability。
- 一个批次只能发送到一个 TIAS 实例；不同批次可以并行发送到不同实例。

## 8. 自适应滑动检测算法

### 8.1 第一阶段：全课程粗扫

按照 `coarse_interval_seconds` 对 T/S 全课程生成初始检测点：

```text
00:00, 00:30, 01:00, 01:30, ...
```

粗扫用于发现候选行为，不负责确定精确边界。只有已经被粗扫发现的行为才能进入后续加密检测。完全发生在两个粗扫点之间的短行为可能漏检，这是该策略无法通过后续细化补救的边界。

### 8.2 第二阶段：候选点聚类

将同一行为相邻或重叠的粗扫命中点合并为候选组。两个相距很远的命中点不能直接推断为持续行为。

例如 `20:00` 和 `30:00` 都命中板书时，应先建立两个候选，分别向左右寻找边界；只有最终事件区间重叠，或事件间缺口不超过板书 `max_gap_seconds`，才合并。

### 8.3 第三阶段：候选窗口扩展

从命中点开始，使用第一层加密间隔向左右搜索，直到两侧都找到“非该行为”的检测点：

```text
19:30 否
19:40 否
19:50 是
20:00 是
20:10 否
```

此时获得两个边界括号：

```text
起点位于 [19:40 否, 19:50 是]
终点位于 [20:00 是, 20:10 否]
```

不能只检测命中点前后各一个时间点。如果左侧仍然为该行为，必须继续向左扩展；右侧同理。

### 8.4 第四阶段：窗口拓扑扫描

使用第一层加密间隔对候选窗口内部完整扫描一次，判断其中是一段行为，还是存在真实中断形成多段行为。

该步骤能够处理两个粗扫命中点之间存在较长非行为区间的情况。之后的更密集扫描不再覆盖整个窗口，只处理状态变化边界和矛盾点。

### 8.5 第五阶段：多分辨率边界细化

对每个“否到是”和“是到否”的不确定区间按配置逐级细化：

```text
10 秒 → 5 秒 → 2 秒 → 1 秒
```

例如：

```text
起点：[19:40 否, 19:50 是]
  5 秒检测 19:45 否 → [19:45 否, 19:50 是]
  2 秒检测 19:47 否、19:49 是 → [19:47 否, 19:49 是]
  1 秒检测 19:48 否 → 起点约 19:49

终点：[20:00 是, 20:10 否]
  5 秒检测 20:05 是 → [20:05 是, 20:10 否]
  2 秒检测 20:07 是、20:09 否 → [20:07 是, 20:09 否]
  1 秒检测 20:08 否 → 终点约 20:08
```

最终事件使用半开区间 `[start, end)` 表示，例如 `[19:49, 20:08)`，边界误差约等于最后一级间隔。

### 8.6 第六阶段：矛盾点补帧

单个反向结果不能立即改变事件边界：

```text
19:56 板书
19:57 板书
19:58 非板书
19:59 板书
20:00 板书
```

`19:58` 是一个内部缺口候选。视觉服务可以读取该点前后帧，或直接在最终时间序列后处理阶段应用缺口合并。对于 `suspected_sitting`、`posture_fallback`、主体缺失或相邻帧冲突，也应生成局部补帧计划。

## 9. 缺口合并与区间语义

### 9.1 合并规则

```text
gap_seconds <= max_gap_seconds → 合并为同一事件
gap_seconds >  max_gap_seconds → 保留为两个事件
```

板书默认允许 3 秒缺口，坐姿默认允许 5 秒缺口。两个值均可配置。

### 9.2 1 到 8 秒、12 到 20 秒案例

如果按 1 秒检测点解释：

```text
1 到 8 秒：板书
9 到 11 秒：非板书
12 到 20 秒：板书
```

统一转换为半开区间：

```text
第一段：[1, 9)
第二段：[12, 21)
gap = 12 - 9 = 3 秒
```

因为 `3 <= writing_max_gap_seconds`，归一化后合并为 `[1, 21)`。如果第二段从 13 秒开始，则缺口为 4 秒，应拆分为两个事件。

建议最终结果同时保留合并前片段和被填补缺口：

```json
{
  "behavior": "WRITING",
  "start_ms": 1000,
  "end_ms": 21000,
  "raw_segments": [[1000, 9000], [12000, 21000]],
  "filled_gaps": [[9000, 12000]],
  "boundary_error_ms": 1000
}
```

## 10. 检测点缓存与去重

多轮扫描容易重复访问相同时间点。视觉服务必须缓存帧和 TIAS 推理结果，建议逻辑键：

```text
course_job_id
+ stream_type
+ timestamp_ms
+ capability
+ model_version
+ roi_version
```

缓存内容至少包括：

- `frame_id`、本地帧路径、时间戳。
- TIAS 请求 `batch_id` 和目标 `instance_id`。
- 原始响应摘要与标准化帧观察结果。
- 模型版本和 ROI 配置版本。

相同键再次出现时直接复用，不重复抽帧和请求 TIAS。若模型版本或 ROI 变化，必须形成新缓存版本。

## 11. 离线批次与有限并发

### 11.1 批次构造

待检测点先去重、排序，再按以下维度分组：

```text
course_job_id
stream_type
capability
model_version
```

组内按 `tias_batch_size` 分批。T 教师行为、T 头部姿态和 S 学生行为不能放在同一批次。

### 11.2 并发执行

视觉服务最多同时发出 `batch_concurrency` 个批次请求：

```text
Batch 1 → TIAS 适配器 → 实例 A
Batch 2 → TIAS 适配器 → 实例 B
Batch 3 → 等待本任务并发槽
```

TIAS 适配器继续根据全局实例容量决定具体实例。如果存在在线实例但全部满载，返回可重试的“暂无容量”；视觉服务延迟后重新提交。适配器不得在内部无限堆积请求。

### 11.3 请求不拆分

TIAS 适配器不把一个图片批次再次拆到多个实例。这样能够保持请求语义、响应顺序和容量占用简单。若批次过大，视觉服务在发送前按配置拆分。

## 12. 建议的内部接口

### 12.1 离线批次接口

```text
POST /internal/v1/tias/analyze-batch
```

请求示意：

```json
{
  "course_job_id": "course-job-001",
  "batch_id": "teacher-writing-round2-0003",
  "capability": "teacher_behavior",
  "stream_type": "teacher",
  "round": 2,
  "items": [
    {
      "frame_id": "teacher-1190000",
      "timestamp_ms": 1190000,
      "storage_path": "/data/course/course-job-001/frames/teacher/teacher-1190000.jpg"
    }
  ]
}
```

响应示意：

```json
{
  "batch_id": "teacher-writing-round2-0003",
  "instance_id": "tias-gpu0-02",
  "model_version": "teacher-behavior-v1",
  "items": [
    {
      "frame_id": "teacher-1190000",
      "timestamp_ms": 1190000,
      "status": "SUCCESS",
      "result": {}
    }
  ]
}
```

### 12.2 在线接口

```text
POST /v1/online/tias/analyze
```

在线请求使用 Base64，并按完整 HTTP 请求选择实例。在线网关不创建课程视觉任务，不进入 Kafka，也不执行事件区间聚合。

## 13. TIAS 实例选择

候选实例必须同时满足：

- 心跳未过期。
- 状态为 `ONLINE`，或符合平台约定的可接单状态。
- 包含请求 capability。
- `max_batch_size` 不小于当前请求图片数量。
- `running_batches + reserved < max_concurrency`。
- 熔断器未打开。

候选排序建议：

```text
1. 当前运行批次 + 本地预占数量
2. 当前进程近期选择次数
3. 平均延迟
4. P95 延迟
5. 排队批次数
6. 近期失败次数
7. instance_id 稳定排序
```

选择后必须先创建带 TTL 的容量预占，调用结束后释放。调用失败时只在可重试错误下切换实例，并且一次重试不再选择本次已经失败的实例。

## 14. 结果聚合与持久化

视觉分析服务聚合以下结果：

- 教师板书事件区间、总时长和课程占比。
- 教师坐姿事件区间、总时长和课程占比。
- 教师姿态时间线和讲授普通统计。
- 学生人数、抬头率、睡觉、玩手机和阅读等统计。
- 关键证据快照和时间戳。
- 课程级指标与评分。

结构化结果写入现有视觉业务数据库。最终快照写入：

```text
/data/result/{course_job_id}/vision/snapshots/{snapshot_id}.jpg
```

普通抽帧保留在 `/data/course`，任务完成后可以清理。长期结果表中只保存业务需要的快照路径，不保存所有中间帧。

## 15. 容错与终止条件

### 15.1 检测级容错

- 单个短反向缺口按行为配置进行合并。
- `suspected_sitting` 和 `posture_fallback` 触发局部复核，不直接作为强负向或强正向。
- 批次部分失败时仅补发失败帧，并保持原 `frame_id`。
- 对同一帧的重复响应按缓存键和模型版本幂等覆盖或忽略。

### 15.2 调度级容错

- 无在线 TIAS 实例：返回不可调度状态。
- 有实例但容量已满：返回可重试的容量状态。
- TIAS 超时或临时故障：有限次数切换实例重试。
- 非法输入或批次超过上限：直接拒绝，不执行无意义重试。

### 15.3 扫描终止条件

任一条件满足时停止继续加密：

- 已达到配置的最小间隔。
- 两侧边界误差均不大于最小间隔。
- 达到 `max_refine_rounds`。
- 达到 `max_detection_points_per_course`。
- 课程任务被取消。

达到预算上限但仍未收敛时，结果标记实际 `boundary_error_ms`，不能伪装成精确到 1 秒。

## 16. 观测指标

视觉分析服务：

- 每课程粗扫点数、加密点数和缓存命中率。
- 每种行为候选数、最终事件数、合并缺口数。
- 每轮扫描耗时和新增 TIAS 调用量。
- 每课程达到的最终边界精度。
- 达到检测点预算或轮次上限的任务数。

TIAS 适配服务：

- 每实例 running、reserved、成功率、平均与 P95 延迟。
- 每 capability 请求数、批次大小分布和图片数。
- 429/503/超时/重试次数。
- 熔断打开次数和实例切换次数。

日志必须携带：

```text
course_job_id
node_run_id
batch_id
round
frame_id
timestamp_ms
capability
instance_id
model_version
```

## 17. 测试场景

### 17.1 自适应扫描

- 粗扫命中一个板书点，左右逐级找到准确边界。
- 粗扫命中多个相邻点，候选窗口去重后只执行一次细化。
- 两个远距离命中点分别展开，不误合并为长事件。
- 候选窗口内部存在真实长中断，拆成两个事件。
- 10、5、2、1 秒配置严格递减并正确停止。

### 17.2 缺口容错

- 板书缺口 1、2、3 秒均合并，4 秒拆分。
- 坐姿缺口 1 到 5 秒均合并，6 秒拆分。
- 单个 `posture_fallback` 不立即结束坐姿区间。
- 原始片段与 `filled_gaps` 在合并后仍可追溯。

### 17.3 批次调度

- 批次大小为 1、配置上限和超过上限。
- 多个批次被有限并发路由到不同实例。
- 同一批次从不跨实例拆分。
- 实例满载、心跳过期、熔断和重试切换。
- 部分帧失败时只补发失败帧。

### 17.4 文件与幂等

- 多轮扫描重复时间点命中缓存。
- 模型版本变化后不复用旧结果。
- 临时帧清理不删除 `/data/result` 快照。
- 相同 `batch_id` 重复返回不产生重复业务记录。

## 18. 实施阶段建议

### 阶段一：逻辑边界明确

- 保留现有 `VisualAnalysisWorker`，将 `RemoteFrameAnalyzer` 封装为明确接口。
- 先支持从 `/data/course` 读取本地 T/S，取消重复下载。
- 将固定 30 秒间隔释放为配置。

### 阶段二：TIAS 适配服务独立

- 抽离注册查询、实例选择、容量预占、重试和协议转换。
- `jy-vision-orchestrator-server` 通过内部 HTTP 接口调用适配服务。
- 在线 TIAS 网关复用同一注册与实例路由能力。

### 阶段三：自适应板书与坐姿

- 实现粗扫、候选窗口、拓扑扫描和边界细化。
- 实现板书 3 秒、坐姿 5 秒缺口合并。
- 保存原始片段、填补缺口和边界误差。

### 阶段四：更多行为策略

- 根据业务验证逐项增加其他行为的精确时间策略。
- 不在没有业务定义和样本验证前强行抽象所有行为的统一规则。

## 19. 关键设计结论

1. 视觉分析服务负责抽帧计划、迭代判断、聚合和入库；TIAS 适配器负责实例调度和协议转换。
2. 初扫间隔、细化间隔、批次大小、批次并发和行为缺口均可配置。
3. 第一层加密扫描候选窗口内部，后续只细化变化边界和矛盾点。
4. 单帧反向结果不会立即切断事件；板书缺口不超过 3 秒、坐姿缺口不超过 5 秒时合并。
5. 事件区间统一使用 `[start, end)`，并保留原始片段、填补缺口和边界误差。
6. 在线请求按完整请求路由；离线图片按批次路由，一个批次不跨实例拆分。
7. 自适应扫描只能细化已被粗扫发现的事件，粗扫间隔决定短行为召回上限。
8. 通过检测缓存、有限并发、轮次上限和总点数预算控制重复调用与资源消耗。

## Context

当前项目是以 FastAPI 为入口的视觉推理服务，已有以下基础能力：

- `app/services/student_behavior_service.py`：人数、人脸、学生行为检测封装。
- `app/services/teacher_behavior_service.py`：教师行为检测封装，使用 `teacher_behavior.pt`。
- `app/services/teacher_head_pose_service.py`：教师头部姿态分析封装，基于 DirectMHP。
- `app/core/settings.py`：模型加载、阈值和运行设备配置。

现有服务主要面向单帧图片 HTTP 推理；本次要补齐的是“课次视频批处理 Worker”能力。Worker 以 Kafka 消息为唯一任务来源，读取完整视频 URL，按固定间隔抽帧，复用现有模型进行分析，并把过程状态、分钟级结果、抓拍图片和指标结果写入 `ai_quality` 数据库。

关键约束和已确认口径：

- 不关心、不写入 `lesson_video`，任务输入以 Kafka 消息为准。
- 视频地址是完整 URL，可直接下载或浏览器打开。
- `lesson_snapshot_event` 只保存核心课堂快照事件，不保存所有 30 秒抽帧图片。
- 抓拍图写入配置的抓拍根目录；NFS 不可写时本地使用项目内 `blobstor/image` 替代，数据库 `image_url` 只保存 `cv/{task_id}/{image_id}.png` 相对路径。
- 入选核心事件的抓拍图缩放到原图 1/4 后长期保存。
- `present_count=0` 的帧跳过，不参与抬头率等均值计算。
- `student_count=0` 或缺失时，应到人数分母使用本次检测到的最高人数兜底。
- 到课率按检测人数中位数计算。
- 分钟缺有效帧不补 0。
- 课程时长以视频实际时长为准。
- 同一 `task_id` 重跑时清理旧时间线、抓拍、统计结果，再写新结果；指标结果 UPSERT。
- 视频不可用等任务级失败重试 3 次，最终失败写失败状态并提交 Kafka offset。
- 第一版先按单课次处理，并发参数配置化；Worker 和 HTTP 服务入口分开。

整体数据流：

```text
Kafka 消息
   │
   ▼
消息校验与幂等锁定
   │
   ├── UPSERT lesson_ai_workflow(status=处理中)
   ├── UPDATE lesson_ai_job(overall_status=处理中)
   │
   ▼
下载教师/学生视频 URL 到临时目录
   │
   ▼
读取视频实际时长，按 30 秒间隔抽帧
   │
   ├── 学生帧：人数、人脸、阅读、睡觉、玩手机等检测
   ├── 教师帧：教师主体、教师行为、头部姿态检测
   └── 核心快照候选：按配置阈值筛选后缩放到 1/4 写入抓拍目录，落相对路径
   │
   ▼
聚合分钟级时间线与课次指标
   │
   ├── lesson_behavior_timeline
   ├── lesson_snapshot_event
   └── indicator_score_result
   │
   ▼
更新 workflow/job 成功或失败状态
   │
   ▼
提交 Kafka offset，清理临时文件
```

## Goals / Non-Goals

**Goals:**

- 新增独立 Worker 入口，支持 Kafka 消费课次视觉分析任务。
- 支持从完整视频 URL 下载学生视角和教师视角视频。
- 支持按 30 秒间隔抽帧，抽帧时间点优先取每个 30 秒窗口中点，例如 15s、45s、75s。
- 复用现有图片推理能力分析学生帧和教师帧。
- 生成并写入以下第一版指标：
  - `E2-01 到课率`：检测人数中位数 / 应到人数。
  - `E3-01 前排就座率`：检测人数乘以 20%～30% 稳定随机比例后取整，再除以应到人数。
  - `E4-01 后排就座率`：检测人数乘以 20%～30% 稳定随机比例后取整，再除以应到人数。
  - `E5-01 平均抬头率`：人脸数 / 人数。
  - `A6-01 面向学生占比`：教师头部姿态正面占比，作为固定机位下的近似口径。
- 支持抓拍保存，落库相对路径；本地可使用项目内目录替代 NFS。
- 支持任务状态闭环、失败重试、幂等重跑。
- 保持现有 HTTP 图片推理接口行为不变。

**Non-Goals:**

- 第一阶段不实现真实前排/后排空间识别；`E3-01`、`E4-01` 是占位指标，不代表真实视觉能力。
- 第一阶段不实现 `A6-02` 面向课件、`A6-03` 面向板书、`A6-04` 面向电脑。
- 第一阶段不实现 `A7-01` 讲台驻留时长、`A7-02` 巡视时长、`A7-03` 巡视次数。
- 第一阶段不实现 `G3-01` 教室上座率。
- 第一阶段不实现完整 DLQ 失败次数管理；失败以落库状态和提交 offset 为准。
- 第一阶段不实现多实例任务均衡调度；仅通过并发配置预留扩展点。
- 第一阶段不改造现有模型训练、模型权重和 HTTP 接口协议。

## Decisions

### 1. 新增独立 Worker 模块，不复用 FastAPI 进程承载批处理

建议新增 `app/ai_quality/` 模块，Worker 启动入口与 `app/main.py` 分离：

- `app/ai_quality/config.py`：Kafka、DB、NFS、抽帧、并发、重试配置。
- `app/ai_quality/message.py`：Kafka 消息结构校验和规范化。
- `app/ai_quality/kafka_consumer.py`：Kafka 消费、手动 offset 提交、重试编排。
- `app/ai_quality/worker.py`：单课次任务编排。
- `app/ai_quality/video.py`：视频下载、时长读取、抽帧、临时文件清理。
- `app/ai_quality/frame_analyzer.py`：复用现有学生/教师模型服务，输出统一帧分析结构。
- `app/ai_quality/aggregator.py`：分钟级聚合、课次指标聚合、占位指标计算。
- `app/ai_quality/storage.py`：NFS 图片缩放保存和相对路径生成。
- `app/ai_quality/db.py`：MySQL 连接、事务、重试。
- `app/ai_quality/repositories.py`：按表封装写入逻辑。
- `app/ai_quality/scoring.py`：按指标规则或线性比例生成 `score`。
- `app/ai_quality/ids.py`：稳定业务 ID 生成。

理由：

- 批处理任务耗时长、依赖 Kafka、DB 和视频 I/O，不适合阻塞 HTTP 服务进程。
- 现有模型服务可以继续复用，减少重复推理逻辑。
- 后续多实例部署时，Worker 可以独立扩缩容。

备选方案：

- 在 FastAPI 中新增后台任务接口：实现快，但任务生命周期、重试、offset 提交和长耗时资源管理会混在 HTTP 层，不适合本次 Kafka 驱动链路。

### 2. Kafka 消费使用手动提交 offset

Worker 消费 Kafka 消息后，只在以下场景提交 offset：

- 任务处理成功，成功写入最终状态。
- 任务级失败重试 3 次后，写入失败状态。
- 消息格式不可处理，写入或记录失败原因后跳过。

理由：

- 手动提交可以避免进程异常退出时消息被提前确认。
- 对于视频不可用、URL 失效等不可恢复错误，重试 3 次后继续阻塞 topic 没有收益，应落库失败并提交 offset。

备选方案：

- 自动提交 offset：实现简单，但任务未真正完成时可能丢消息。
- 完整 DLQ：更完整，但设计文档未明确死信表/死信 topic 管理细节，第一阶段先不做。

### 3. 任务输入只信 Kafka 消息，不依赖 `lesson_video`

Kafka 消息必须携带或能解析出本次分析所需的关键字段：

- `task_id`
- 学生视频 URL
- 教师视频 URL
- `student_count`，可缺失或为 0
- 可选的学校、班级、课程、时间等上下文字段

Worker 不查询、不写入 `lesson_video`。

理由：

- 用户已确认 `lesson_video` 不是本服务需要关心的表。
- 降低第一版对外部业务表状态的耦合。

备选方案：

- 通过 `lesson_video` 补全视频地址：如果表状态和 Kafka 消息不一致，会引入额外数据口径冲突。

### 4. 视频抽帧按实际时长和 30 秒窗口中点执行

抽帧策略：

- 获取视频实际时长 `duration_seconds`。
- 每 30 秒一个窗口，抽帧时间点取窗口中点：`15 + 30 * n`。
- 若最后窗口不足 30 秒，但中点小于实际时长，则抽取；否则不抽取。
- 学生视频和教师视频分别抽帧，按分钟号或时间戳关联聚合。

理由：

- 中点帧比窗口边界帧更能代表该窗口。
- 实际时长优先于外部传入时长，避免视频剪辑、上传缺失导致统计偏差。

备选方案：

- 每分钟抽一帧：计算成本更低，但不足以覆盖 30 秒级时间线。
- 每秒抽帧：质量更高但成本过大，第一版不采用。

### 5. 指标分为真实口径、近似口径、占位口径

第一版必须在代码和文档中区分指标可信度：

| 指标 | 类型 | 口径 |
|---|---|---|
| `E2-01 到课率` | 真实检测聚合 | 检测人数中位数 / 应到人数 |
| `E5-01 平均抬头率` | 真实检测聚合 | 有效帧 `face_count / present_count` 的聚合结果 |
| `A6-01 面向学生占比` | 近似检测聚合 | 教师头部姿态 `FaceDirection=front` 且非低头的有效帧占比 |
| `E3-01 前排就座率` | 占位指标 | 检测人数 * 20%～30% 稳定随机比例后取整 / 应到人数 |
| `E4-01 后排就座率` | 占位指标 | 检测人数 * 20%～30% 稳定随机比例后取整 / 应到人数 |

稳定随机规则：

- 不使用完全随机数。
- 使用 `task_id + minute_no + metric_type` 作为 seed。
- 每次重跑同一任务得到相同占位结果。
- 比例范围为 `[0.20, 0.30]`，人数取整数，建议使用 `round` 后限制在 `[0, present_count]`。

理由：

- 用户明确要求第一版前后排用“人数 * 20%～30% 随机取整数”的假数据。
- 稳定随机能兼顾“看起来有变化”和“重跑可复现”。

备选方案：

- 完全随机：重跑结果不一致，破坏幂等。
- 固定 25%：实现最简单，但数据缺少波动，不符合用户要求。

### 6. `present_count=0` 的帧跳过，不参与均值

学生帧聚合规则：

- `present_count <= 0` 时，该帧不参与抬头率、前排占位率、后排占位率均值计算。
- 到课率使用有效人数序列的中位数；若所有帧无有效人数，则结果按 0 处理并保留异常信息。
- 应到人数 `student_count > 0` 时使用 `student_count`；否则使用本任务检测到的最高 `present_count` 作为兜底分母。

理由：

- 人数为 0 的帧通常代表空镜头、抽帧异常或检测失败，补 0 会显著拉低均值。
- 用最高检测人数兜底比固定 1 或直接失败更符合用户确认的数据口径。

备选方案：

- 人数 0 参与均值：会把检测失败混入真实课堂表现。
- 缺少 `student_count` 直接失败：会降低第一版任务成功率。

### 7. 核心快照事件筛选与图片存储

`lesson_snapshot_event` 不是抽帧归档表，而是报告侧核心课堂瞬间事件表。所有 30 秒抽帧默认只作为模型分析和指标聚合的过程数据；只有命中配置化快照策略的候选帧，才保存图片并写入 `lesson_snapshot_event`。

快照事件类型：

| 事件 | target_type | record_type | behavior_type | 默认策略 |
|---|---:|---:|---:|---|
| 教态预警 | 1 | 1 | 1 | 连续 3 个有效教师帧非面向学生或低头 |
| 学生抬头高峰 | 2 | 2 | 2 | 按 `face_count / present_count` 取最高 3 张，再过滤 `>=0.70` |
| 学生阅读专注 | 2 | 2 | 3 | 按 `read_count / present_count` 取最高 3 张，再过滤最低阅读占比 |
| 学生睡觉 | 2 | 1 | 4 | `sleep_count >= 2` 或 `sleep_count / present_count >= 0.05` |
| 学生玩手机 | 2 | 1 | 5 | `phone_count >= 2` 或 `phone_count / present_count >= 0.05` |

配置规则：

- 所有阈值、TopK 和总保存上限均从 `config.toml` 读取。
- 睡觉和玩手机不单独配置每类保存上限，只受全局总保存上限和同类最小时间间隔约束。
- 同类候选需要按严重程度或代表性排序，优先保存更有代表性的图片。
- 同类快照应支持最小时间间隔，避免相邻采样点重复刷屏。
- 总保存上限默认 30 条，符合概要设计中每课次 `0~30` 条的数据量估算。

抓拍写入规则：

- 配置 `snapshot_mount_root`，本地默认使用项目内 `blobstor/image`；NFS 恢复后可改为实际挂载目录，例如 `/mnt`。
- 生成相对路径 `cv/{task_id}/{image_id}.png`。
- 实际写入路径为 `{snapshot_mount_root}/cv/{task_id}/{image_id}.png`。
- 图片写入前按宽高各 1/4 缩放。
- `lesson_snapshot_event.image_url` 只保存相对路径，不拼 HTTP 根地址。

理由：

- 用户已确认数据库只需要保存相对路径。
- HTTP 访问根地址可以由前端或资源服务统一映射，Worker 不需要耦合展示域名。
- 只保存核心事件可以避免将所有抽帧误当作课堂亮点或异常，同时控制报告展示噪音和存储规模。

备选方案：

- Worker 拼完整 HTTP URL：需要额外确认访问根地址，且以后域名变化会导致历史数据迁移。

### 8. 数据库写入按仓储封装，任务结果按事务边界分段提交

写入策略：

- 任务开始：UPSERT `lesson_ai_workflow(stage_node=7,status=2,progress=0)`，更新 `lesson_ai_job(overall_status=2,started_at=NOW())`。
- 重跑清理：按 `task_id` 删除旧 `lesson_behavior_timeline`、`lesson_snapshot_event`、`lesson_student_behavior_stat`；`indicator_score_result` 后续 UPSERT 覆盖。
- 分析过程中：可批量写入时间线和抓拍事件，减少单行提交开销。
- 学生异常行为统计：写入 `lesson_student_behavior_stat`，第一版只覆盖 `behavior_type=1` 玩手机和 `behavior_type=3` 趴桌睡觉。
- 任务成功：UPSERT 指标结果，更新 workflow/job 为成功。
- 任务失败：记录失败状态和错误摘要，提交 offset。

理由：

- 数据表已有唯一键约束，适合用 UPSERT 保证幂等。
- 分段事务避免单个长事务覆盖视频下载和模型推理全过程。

备选方案：

- 一个任务全程单事务：失败回滚容易，但长事务会持有连接和锁，不适合视频分析。

### 9. `lesson_student_behavior_stat` 第一版统计口径

`lesson_student_behavior_stat` 是课次学生异常行为聚合统计表，不保存逐帧明细。第一版仅写入当前模型已能稳定输出的两类异常：

| behavior_type | 行为 | 取数来源 |
|---:|---|---|
| 1 | 玩手机 | `StudentFrameMetric.phone_count` |
| 3 | 趴桌睡觉 | `StudentFrameMetric.sleep_count` |

不写入交头接耳、离座和其他异常，因为当前第一版模型链路未形成这些行为的稳定课次级口径。

统计窗口：

- 固定跳过课程前 3 分钟，避免课前准备、未正式上课等画面污染统计。
- 默认从 `minute_no >= 3` 的帧开始统计，也就是从视频 03:00 处往后。
- `BehaviorStatStartMinute` 释放到 `config.toml`，默认值为 3。
- `present_count <= 0` 的学生帧不参与统计。
- `detect_count` 和 `peak_period_desc` 使用同一个统计窗口，避免总次数和高峰时段口径不一致。

`detect_count`：

- 同一 `task_id + behavior_type` 只写一行。
- `detect_count` 为统计窗口内该行为检测人次累计值。
- 若某个行为累计值为 0，则不写该行为行。
- `confidence_level` 第一版固定写 2，表示中等置信度。

`peak_period_desc` 生成：

1. 按分钟聚合行为人数，例如同一分钟有两个抽帧点，则累加该分钟的 `phone_count` 或 `sleep_count`。
2. 只保留分钟聚合值大于 0 的分钟。
3. 将连续异常分钟合并为候选段。
4. 每个候选段计算：
   - `segment_total`：候选段内行为总人次。
   - `segment_peak`：候选段内单分钟最高人次。
   - `segment_length`：候选段持续分钟数。
   - `start_minute`：候选段开始分钟。
5. 候选段排序规则：
   - `segment_total` 倒序。
   - `segment_peak` 倒序。
   - `segment_length` 倒序。
   - `start_minute` 正序。
6. 从排序后的候选段中最多取 `BehaviorStatPeakMaxSegments` 段，默认 5。
7. 最终展示时按时间正序拼接，例如 `3′–5′、10′–13′`。

理由：

- 用户已确认课前 3 分钟不代表真实上课状态。
- 先按候选段严重程度排序，再确定展示范围，可以避免较早但轻微的异常段挤掉后续更集中的异常段。
- 最多 5 段可以兼顾报告可读性和异常集中时段表达。

### 10. 分数计算先采用可配置策略

`indicator_score_result` 需要同时写入指标值和分数。第一版建议策略：

- 优先读取 `indicator.score_rule`，如果规则可解析则按规则计算。
- 如果规则不可解析或为空，则按比例指标默认 `score = clamp(value, 0, 1) * 100`。
- 内部聚合值使用 0～1 的比例值；写入 `indicator_score_result.raw_value` 时，如 `indicator.unit` 为 `%`，转换为 0～100 的百分值，以匹配数据库中的 `score_rule` 阈值。

理由：

- 数据库已有 `indicator` 表和规则字段，保留后续按规则评分空间。
- 第一版先保证结果可落库、可展示、可回放。

备选方案：

- 只写指标值不写分数：可能不满足下游读取 `score` 的预期。
- 写死所有指标规则：会和数据库配置重复。

### 11. 第一版并发默认单课次，配置化预留

配置建议：

- `worker_concurrency=1`
- `max_task_retries=3`
- `frame_interval_seconds=30`
- `snapshot_scale=0.25`
- `snapshot_relative_prefix=cv`
- `kafka_enable_dlq=false`

理由：

- 当前用户确认先处理 1 节课。
- 视频推理是 GPU/CPU 密集型任务，并发过高容易拖垮推理性能。

备选方案：

- 默认多课次并发：吞吐更高，但第一版联调风险更大。

## Risks / Trade-offs

- [Risk] `A6-01` 只能近似“教师正面朝向学生”，不能可靠区分教师是否真正看向学生群体。  
  Mitigation：文档和代码注释明确 `A6-01` 是固定机位下的近似口径；不扩展到课件、电脑、板书。

- [Risk] `E3-01`、`E4-01` 是占位指标，容易被误解为真实前后排识别。  
  Mitigation：在设计、规格、任务和代码命名中明确 `placeholder` 或 `mock` 语义；后续真实前后排识别作为单独阶段替换。

- [Risk] Kafka 消息实际字段可能和设计文档存在差异。  
  Mitigation：消息解析层做字段映射和校验，保留原始消息摘要；缺少关键字段时写失败状态并提交 offset。

- [Risk] 抓拍目录不可写会导致抓拍失败。  
  Mitigation：本地默认使用项目内 `blobstor/image` 替代 NFS；启动时检查抓拍根目录可写；任务中保存失败时记录错误并按任务失败处理，避免产生缺图片的半成功数据。

- [Risk] 视频 URL 下载慢或中断。  
  Mitigation：配置下载超时、重试和临时文件清理；任务级失败按 3 次重试。

- [Risk] 模型推理异常可能导致 Worker 进程退出。  
  Mitigation：单帧分析捕获异常并记录，任务级异常进入失败状态；进程级崩溃依赖 Kafka 未提交 offset 重放。

- [Risk] 长视频批量写入数据库耗时较长。  
  Mitigation：时间线和抓拍事件批量写入；任务状态分段提交，避免长事务。

## Migration Plan

1. 增加 Worker 代码和配置项，不改变现有 HTTP 服务。
2. 在测试环境配置 Kafka、MySQL、NFS 挂载目录和模型文件。
3. 使用 `tests`、`tests2` 中现有图片/视频样例验证单帧模型复用和头部姿态能力。
4. 使用本地 Kafka 投递单条课次消息，验证完整链路：消费、下载、抽帧、推理、NFS 写图、DB 写入、offset 提交。
5. 验证同一 `task_id` 重跑结果可覆盖旧数据，且前后排占位指标稳定可复现。
6. 验证视频不可用场景：重试 3 次后写失败状态并提交 offset。
7. 第一阶段上线时默认 `worker_concurrency=1`，观察资源占用后再调高。

回滚策略：

- Worker 独立部署，出现问题时停止 Worker 进程即可，不影响现有 HTTP 图片推理服务。
- 已写入的某个 `task_id` 结果可通过重跑清理并覆盖。

## Open Questions

- Kafka broker、topic、consumer group 最终以部署配置为准；当前设计不硬编码。
- Kafka 消息字段名需要在联调时用真实样例最终确认，解析层需要支持兼容映射。
- `indicator.score_rule` 的具体表达式格式需要在实现时验证；若不可解析，第一版使用默认比例评分兜底。

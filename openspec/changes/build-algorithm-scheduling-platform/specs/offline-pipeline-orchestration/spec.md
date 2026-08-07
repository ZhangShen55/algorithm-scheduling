## ADDED Requirements

### Requirement: 可靠的异步受理
control service SHALL 在同一个 PostgreSQL 事务中保存课程任务状态和 Outbox 事件。orchestrator service SHALL 将待发布的 Outbox 事件发送到 Kafka，并根据消费到的事件幂等初始化管道。

#### Scenario: API 进程在事务提交后停止
- **WHEN** 任务事务已经提交，但 API 进程在发布 Kafka 消息前停止
- **THEN** Outbox 事件仍可被发现，并在 Publisher 恢复后发布

### Requirement: 四条相互独立的离线管道
orchestrator SHALL 将请求的任务类型展开为 `PPT_SLICE -> PPT_OCR -> PPT_KEYWORDS`、`ASR_TRANSCRIPTION -> COURSE_OVERVIEW`、教师自适应视觉分析和学生自适应视觉分析，并且只创建 `task_types` 选中的管道。

#### Scenario: 同时执行 ASR 和教师行为
- **WHEN** 单个请求使用同一个教师视频 URL 选择 ASR 和教师行为
- **THEN** 本次执行只下载一次教师视频，并由两条管道共享

#### Scenario: 后续单独请求
- **WHEN** 教师行为在较早请求中已经完成，之后才请求 ASR
- **THEN** 重新下载教师视频，不假设此前提取的 WAV 或源视频仍然保留

### Requirement: 动态 PPT 子任务
orchestrator SHALL 为每个生成的 `ppt_image_id` 创建 OCR 和关键词任务。每张 PPT 图片 SHALL 在切片、OCR 和关键词提取过程中保持同一身份；可配置并发量 SHALL 受可用实例容量约束。

#### Scenario: 生成三十张切片
- **WHEN** PPT 切片生成 30 张有效图片
- **THEN** 管道按 `ppt_image_id` 跟踪 30 个 OCR 项和 30 个对应的关键词项

### Requirement: 两级非抢占式优先级
平台 SHALL 支持 `URGENT` 和 `NORMAL`，默认值为 `NORMAL`；任务类型请求的优先级 SHALL 传递给其节点；对于相同能力，等待中的 `URGENT` 节点 SHALL 先于等待中的 `NORMAL` 节点被选择。已经运行的任务 SHALL 不被中断。

#### Scenario: 普通任务之后到达紧急任务
- **WHEN** 一个 NORMAL OCR 调用正在运行，同时一个 URGENT OCR 节点变为就绪
- **THEN** 运行中的调用正常完成，随后释放的 OCR 容量优先分配给 URGENT 节点，再分配给等待中的 NORMAL 节点

### Requirement: 感知算子状态的等待
当节点所需的算子能力没有已注册且就绪的实例时，节点 SHALL 保持 `status=30` 并给出中文原因，同时不阻塞无关管道。

#### Scenario: OCR 不可用但 PPT 切片可用
- **WHEN** PPT 切片完成，但没有 OCR 实例就绪
- **THEN** `PPT_SLICE` 保持完成，`PPT_OCR` 等待算子，并且切片的 `path` 和 `count` 仍可查询

### Requirement: Kafka 事件只携带元数据
Kafka 消息 SHALL 只包含标识符、任务类型、优先级、本地路径和编排元数据。视频、音频、Base64 图片和图片二进制数据 SHALL 不进入 Kafka。

#### Scenario: 启动视觉管道
- **WHEN** orchestrator 发布视觉分析命令
- **THEN** 事件引用 `task_id` 和本地视频路径，不嵌入媒体字节

## ADDED Requirements

### Requirement: Kafka 驱动课次视觉分析任务
系统 SHALL 提供独立于 HTTP 服务的视觉分析 Worker，并 SHALL 以 Kafka 消息作为课次分析任务的唯一触发来源。

#### Scenario: 成功消费有效课次任务
- **WHEN** Worker 从配置的 Kafka topic 消费到包含 `task_id`、学生视频 URL、教师视频 URL 的有效消息
- **THEN** 系统 SHALL 创建或更新该 `task_id` 的工作流状态为处理中，并开始课次视频分析

#### Scenario: 不依赖 lesson_video 表
- **WHEN** Worker 处理 Kafka 任务消息
- **THEN** 系统 MUST NOT 读取或写入 `lesson_video` 表来决定视频地址或任务状态

#### Scenario: 消息缺少关键字段
- **WHEN** Kafka 消息缺少 `task_id` 或缺少可用视频 URL
- **THEN** 系统 SHALL 记录失败原因，标记任务失败，并提交该 Kafka offset

### Requirement: 视频下载与抽帧
系统 SHALL 支持从完整视频 URL 下载教师视频和学生视频，并 SHALL 按视频实际时长进行 30 秒间隔抽帧。

#### Scenario: 按窗口中点抽帧
- **WHEN** 视频时长足够覆盖多个 30 秒窗口
- **THEN** 系统 SHALL 优先在 15s、45s、75s 等窗口中点抽取分析帧

#### Scenario: 使用实际视频时长
- **WHEN** Kafka 消息或外部数据中的课程时长与视频实际时长不一致
- **THEN** 系统 SHALL 以视频实际可读取时长作为抽帧和统计范围

#### Scenario: 视频不可用
- **WHEN** 视频 URL 无法下载、无法打开或无法读取有效帧
- **THEN** 系统 SHALL 将该任务作为任务级失败处理，最多重试 3 次后写入失败状态并提交 Kafka offset

### Requirement: 复用现有视觉模型分析帧
系统 SHALL 复用当前项目已有的学生行为、人数、人脸、教师行为和教师头部姿态能力对抽帧图片进行分析。

#### Scenario: 学生帧分析
- **WHEN** 系统处理学生视频抽帧
- **THEN** 系统 SHALL 输出该帧的检测人数、人脸数、睡觉人数、玩手机人数等学生侧分析结果

#### Scenario: 教师帧分析
- **WHEN** 系统处理教师视频抽帧
- **THEN** 系统 SHALL 输出教师主体、教师行为和教师头部姿态结果

#### Scenario: 教师头部姿态不可用
- **WHEN** 教师帧未检测到教师主体或未检测到有效头部姿态
- **THEN** 系统 SHALL 将该教师帧从 `A6-01 面向学生占比` 的有效帧分母中排除

### Requirement: 抓拍图片存储
系统 SHALL 仅将命中核心快照事件策略的抓拍图片缩放到原图 1/4 后写入配置的抓拍根目录，并 SHALL 在数据库中保存相对路径。

#### Scenario: 不保存所有抽帧图片
- **WHEN** 系统按 30 秒间隔抽取分析帧
- **THEN** 系统 MUST NOT 默认将所有抽帧图片写入 `lesson_snapshot_event`
- **AND** 系统 SHALL 仅在抽帧命中核心快照事件策略后写入 `lesson_snapshot_event`

#### Scenario: 保存抓拍图片
- **WHEN** 系统生成抓拍事件
- **THEN** 系统 SHALL 将图片写入 `{snapshot_mount_root}/cv/{task_id}/{image_id}.png`
- **AND** 系统 SHALL 在 `lesson_snapshot_event.image_url` 中保存 `cv/{task_id}/{image_id}.png`

#### Scenario: 不拼接访问根地址
- **WHEN** 系统写入 `lesson_snapshot_event.image_url`
- **THEN** 系统 MUST NOT 拼接 HTTP 域名或 OSS 访问根地址

#### Scenario: 抓拍目录不可写
- **WHEN** 配置的抓拍根目录不存在或不可写
- **THEN** 系统 SHALL 将当前任务标记失败并记录错误原因

### Requirement: 核心快照事件策略
系统 SHALL 按配置化阈值和上限生成 `lesson_snapshot_event`，并 SHALL 支持学生抬头高峰、学生阅读专注、学生睡觉、学生玩手机和教师教态预警事件。

#### Scenario: 学生抬头高峰快照
- **WHEN** 学生帧存在 `present_count > 0`
- **THEN** 系统 SHALL 按 `face_count / present_count` 从高到低选取配置的 TopK 候选
- **AND** 系统 SHALL 仅保存抬头率大于等于配置最低阈值的候选
- **AND** 系统 SHALL 写入 `target_type=2`、`record_type=2`、`behavior_type=2`

#### Scenario: 学生阅读专注快照
- **WHEN** 学生帧存在 `present_count > 0`
- **THEN** 系统 SHALL 按 `read_count / present_count` 从高到低选取配置的 TopK 候选
- **AND** 系统 SHALL 仅保存阅读占比大于等于配置最低阈值的候选
- **AND** 系统 SHALL 写入 `target_type=2`、`record_type=2`、`behavior_type=3`

#### Scenario: 学生睡觉快照
- **WHEN** 学生帧存在 `present_count > 0`
- **THEN** 系统 SHALL 在 `sleep_count` 大于等于配置人数阈值，或 `sleep_count / present_count` 大于等于配置占比阈值时生成候选
- **AND** 系统 SHALL 写入 `target_type=2`、`record_type=1`、`behavior_type=4`

#### Scenario: 学生玩手机快照
- **WHEN** 学生帧存在 `present_count > 0`
- **THEN** 系统 SHALL 在 `phone_count` 大于等于配置人数阈值，或 `phone_count / present_count` 大于等于配置占比阈值时生成候选
- **AND** 系统 SHALL 写入 `target_type=2`、`record_type=1`、`behavior_type=5`

#### Scenario: 教师教态预警快照
- **WHEN** 教师帧存在连续配置帧数的有效头部姿态，且每帧均非面向学生或低头
- **THEN** 系统 SHALL 生成教态预警候选
- **AND** 系统 SHALL 写入 `target_type=1`、`record_type=1`、`behavior_type=1`

#### Scenario: 快照总量控制
- **WHEN** 核心快照候选数量超过配置的每课次总上限
- **THEN** 系统 SHALL 按事件优先级和候选分值保留不超过配置总上限的快照

#### Scenario: 同类快照去重
- **WHEN** 同类快照候选在配置的最小时间间隔内连续出现
- **THEN** 系统 SHALL 优先保留分值更高或更严重的候选，避免相邻帧重复入库

### Requirement: 任务状态与数据库写入闭环
系统 SHALL 将课次分析过程状态、分钟级时间线、抓拍事件和指标结果写入 `ai_quality` 数据库。

#### Scenario: 任务开始
- **WHEN** Worker 开始处理有效任务
- **THEN** 系统 SHALL 写入或更新 `lesson_ai_workflow` 的视觉分析阶段为处理中
- **AND** 系统 SHALL 更新 `lesson_ai_job` 为处理中状态

#### Scenario: 任务成功
- **WHEN** Worker 完成视频分析、图片保存和指标聚合
- **THEN** 系统 SHALL 写入 `lesson_behavior_timeline`
- **AND** 系统 SHALL 写入 `lesson_snapshot_event`
- **AND** 系统 SHALL 写入或更新 `indicator_score_result`
- **AND** 系统 SHALL 将 `lesson_ai_workflow` 和 `lesson_ai_job` 更新为成功状态

#### Scenario: 任务失败
- **WHEN** Worker 在任务级处理过程中最终失败
- **THEN** 系统 SHALL 将 `lesson_ai_workflow` 和 `lesson_ai_job` 更新为失败状态
- **AND** 系统 SHALL 记录可排查的错误摘要
- **AND** 系统 SHALL 提交 Kafka offset

### Requirement: 学生异常行为聚合统计
系统 SHALL 将学生异常行为课次级聚合结果写入 `lesson_student_behavior_stat`，第一版 SHALL 仅覆盖玩手机和趴桌睡觉。

#### Scenario: 写入玩手机统计
- **WHEN** 统计窗口内存在学生玩手机检测结果
- **THEN** 系统 SHALL 写入或更新 `lesson_student_behavior_stat`
- **AND** `behavior_type` SHALL 为 `1`
- **AND** `detect_count` SHALL 为统计窗口内 `phone_count` 的累计人次

#### Scenario: 写入趴桌睡觉统计
- **WHEN** 统计窗口内存在学生趴桌睡觉检测结果
- **THEN** 系统 SHALL 写入或更新 `lesson_student_behavior_stat`
- **AND** `behavior_type` SHALL 为 `3`
- **AND** `detect_count` SHALL 为统计窗口内 `sleep_count` 的累计人次

#### Scenario: 跳过课程前 3 分钟
- **WHEN** 学生行为出现在配置的统计开始分钟之前
- **THEN** 系统 MUST NOT 将这些行为计入 `detect_count`
- **AND** 系统 MUST NOT 将这些行为计入 `peak_period_desc`

#### Scenario: 生成高峰时段描述
- **WHEN** 同一行为在统计窗口内存在多个异常候选段
- **THEN** 系统 SHALL 按候选段总人次、单分钟峰值、持续分钟数和开始分钟排序
- **AND** 系统 SHALL 最多保留配置的高峰段数量
- **AND** 系统 SHALL 将选中的候选段按时间顺序写入 `peak_period_desc`

#### Scenario: 无异常不写统计行
- **WHEN** 统计窗口内某类学生异常行为累计人次为 0
- **THEN** 系统 MUST NOT 为该行为写入 `lesson_student_behavior_stat`

#### Scenario: 第一版不写未支持行为
- **WHEN** 系统生成学生异常行为聚合统计
- **THEN** 系统 MUST NOT 在第一阶段写入交头接耳、离座或其他异常行为统计

### Requirement: 到课率指标
系统 SHALL 生成 `E2-01 到课率` 指标，指标值 SHALL 使用检测人数中位数除以应到人数。

#### Scenario: 正常计算到课率
- **WHEN** 任务存在有效学生帧且 `student_count` 大于 0
- **THEN** 系统 SHALL 使用有效帧检测人数的中位数除以 `student_count` 得到 `E2-01` 指标值

#### Scenario: 应到人数缺失
- **WHEN** `student_count` 缺失或等于 0 且任务存在有效检测人数
- **THEN** 系统 SHALL 使用本任务检测到的最高人数作为应到人数分母

#### Scenario: 无有效人数
- **WHEN** 任务不存在任何有效检测人数
- **THEN** 系统 SHALL 将 `E2-01` 指标值按 0 写入，并在任务日志或错误摘要中保留无有效人数信息

### Requirement: 平均抬头率指标
系统 SHALL 生成 `E5-01 平均抬头率` 指标，指标值 SHALL 使用人脸数除以人数的聚合结果。

#### Scenario: 正常计算抬头率
- **WHEN** 学生帧 `present_count` 大于 0
- **THEN** 系统 SHALL 计算该帧 `face_count / present_count` 作为有效帧抬头率

#### Scenario: 跳过零人数帧
- **WHEN** 学生帧 `present_count` 等于 0
- **THEN** 系统 SHALL 跳过该帧，且该帧 MUST NOT 参与 `E5-01` 均值或中位数计算

### Requirement: 前后排就座率占位指标
系统 SHALL 生成 `E3-01 前排就座率` 和 `E4-01 后排就座率` 占位指标，指标值 SHALL 基于检测人数的 20%～30% 稳定随机整数计算。

#### Scenario: 计算前排占位人数
- **WHEN** 学生帧 `present_count` 大于 0
- **THEN** 系统 SHALL 使用 `task_id + minute_no + metric_type` 作为稳定随机种子，在 20%～30% 范围内生成比例
- **AND** 系统 SHALL 将 `present_count * ratio` 取整后作为前排占位人数

#### Scenario: 计算后排占位人数
- **WHEN** 学生帧 `present_count` 大于 0
- **THEN** 系统 SHALL 使用 `task_id + minute_no + metric_type` 作为稳定随机种子，在 20%～30% 范围内生成比例
- **AND** 系统 SHALL 将 `present_count * ratio` 取整后作为后排占位人数

#### Scenario: 重跑占位结果稳定
- **WHEN** 同一个 `task_id` 使用相同分钟号和指标类型重跑
- **THEN** 系统 SHALL 生成相同的前排和后排占位人数

#### Scenario: 零人数帧不参与前后排指标
- **WHEN** 学生帧 `present_count` 等于 0
- **THEN** 系统 SHALL 跳过该帧，且该帧 MUST NOT 参与 `E3-01` 或 `E4-01` 聚合计算

### Requirement: 面向学生占比指标
系统 SHALL 生成 `A6-01 面向学生占比` 指标，指标值 SHALL 使用教师头部姿态正面有效帧占比作为近似口径。

#### Scenario: 正常计算面向学生占比
- **WHEN** 教师帧存在有效头部姿态，且 `FaceDirection` 为正面并且不是低头
- **THEN** 系统 SHALL 将该帧计入 `A6-01` 的面向学生帧

#### Scenario: 教师姿态无效帧跳过
- **WHEN** 教师帧没有教师主体、没有有效头部姿态或头部姿态状态失败
- **THEN** 系统 SHALL 跳过该帧，且该帧 MUST NOT 参与 `A6-01` 分母

#### Scenario: 不扩展到课件板书电脑
- **WHEN** 系统生成教师朝向相关指标
- **THEN** 系统 MUST NOT 在第一阶段生成 `A6-02`、`A6-03`、`A6-04` 指标结果

### Requirement: 幂等重跑
系统 SHALL 支持同一 `task_id` 的幂等重跑，重跑结果 SHALL 覆盖旧分析结果。

#### Scenario: 重跑前清理旧明细
- **WHEN** Worker 开始处理已存在结果的 `task_id`
- **THEN** 系统 SHALL 删除该 `task_id` 旧的 `lesson_behavior_timeline`、`lesson_snapshot_event` 和 `lesson_student_behavior_stat` 数据

#### Scenario: 指标结果覆盖
- **WHEN** Worker 写入 `indicator_score_result`
- **THEN** 系统 SHALL 按 `task_id` 和 `indicator_id` 执行插入或更新，避免同一指标重复数据

#### Scenario: 重跑成功后状态正确
- **WHEN** 同一 `task_id` 重跑成功
- **THEN** 系统 SHALL 保留本次重跑生成的最新时间线、抓拍事件和指标结果

### Requirement: 并发与部署边界
系统 SHALL 支持通过配置控制 Worker 并发数，第一阶段默认 SHALL 按单课次并发处理。

#### Scenario: 默认单课次处理
- **WHEN** 未显式配置 Worker 并发数
- **THEN** 系统 SHALL 同一时间只处理 1 个课次任务

#### Scenario: Worker 与 HTTP 服务分离
- **WHEN** 启动视觉分析 Worker
- **THEN** 系统 SHALL 使用独立启动入口运行，且 MUST NOT 要求启动 FastAPI HTTP 服务

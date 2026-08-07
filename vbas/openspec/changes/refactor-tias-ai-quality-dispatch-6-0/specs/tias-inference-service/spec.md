## ADDED Requirements

### Requirement: TIAS 6.0 必须使用 `tias` 包名和启动入口
MUST：以下场景为本要求的强制验收条件。
TIAS 6.0 的正式代码包、服务目录和启动入口必须从当前 `app` 迁移为 `tias`。

#### Scenario: 启动入口使用 tias
- **WHEN** TIAS 6.0 通过本地命令、脚本或容器启动
- **THEN** 启动入口必须使用 `tias.main:app`，不再使用 `app.main:app`

#### Scenario: 内部导入使用 tias
- **WHEN** TIAS 内部模块互相引用
- **THEN** 代码必须使用 `tias.*` 导入，不应新增 `app.*` 导入

#### Scenario: 模型和 vendor 默认路径迁移
- **WHEN** TIAS 读取默认模型、DirectMHP 依赖或配置样例路径
- **THEN** 默认路径必须指向 `tias/models` 和 `tias/vendor/DirectMHP`

### Requirement: TIAS 6.0 必须只暴露聚焦的推理和运维接口
MUST：以下场景为本要求的强制验收条件。
TIAS 6.0 必须只暴露课堂质量推理、实例状态、健康检查和排空运维相关接口。

#### Scenario: 必要接口可用
- **WHEN** TIAS 6.0 成功启动
- **THEN** 它必须暴露学生推理、教师推理、worker 状态、健康检查和排空接口

#### Scenario: 课堂质量调度不使用旧接口
- **WHEN** ai_quality 调度课堂质量视觉推理任务
- **THEN** 它不得调用 `/AE/SyncTasks`、`/AE/SyncTasks2`、`/AE/Capacity` 或 `/AE/Capacity_v2`

### Requirement: TIAS 6.0 必须通过配置隐藏旧同步任务接口
MUST：以下场景为本要求的强制验收条件。
TIAS 6.0 必须保留 `/AE/SyncTasks` 和 `/AE/SyncTasks2` 的代码能力，但是否暴露路由地址必须由配置控制。

#### Scenario: 旧同步路由关闭
- **WHEN** `TiasExposeLegacySyncTasks` 为 false
- **THEN** TIAS 不得暴露 `/AE/SyncTasks` 或 `/AE/SyncTasks2`

#### Scenario: 旧同步路由打开
- **WHEN** `TiasExposeLegacySyncTasks` 为 true
- **THEN** TIAS 可以暴露 `/AE/SyncTasks` 和 `/AE/SyncTasks2`，但课堂质量调度链路必须与这些接口保持独立

### Requirement: TIAS 6.0 必须移除过时接口
MUST：以下场景为本要求的强制验收条件。
TIAS 6.0 必须从默认接口面移除过时的 Capacity、Version、LogLevel 和重复学生 v1.0.1 路由地址。

#### Scenario: 过时路由不存在
- **WHEN** TIAS 6.0 成功启动
- **THEN** 它不得暴露 `/AE/Capacity`、`/AE/Capacity_v2`、`/AE/Version`、`/AE/LogLevel` 或 `/ImageDetect/student/v1.0.1`

### Requirement: TIAS 6.0 必须在 v1.0.0 提供学生推理能力
MUST：以下场景为本要求的强制验收条件。
TIAS 必须在 `/ImageDetect/student/v1.0.0` 提供学生推理能力，并使用当前包含 `person_count.pt`、`face_count.pt` 和 `student.pt` 的并行模型逻辑。

#### Scenario: 学生推理成功
- **WHEN** ai_quality 向 `/ImageDetect/student/v1.0.0` 提交学生帧
- **THEN** TIAS 必须为每帧返回一条结果，包含 `present_count`、`face_count`、`sleep_count`、`phone_count` 和 `read_count`

#### Scenario: 学生结果保留帧身份
- **WHEN** TIAS 返回学生推理结果
- **THEN** 每条结果必须包含 `frame_id`、`frame_index` 和 `timestamp_seconds`

### Requirement: TIAS 6.0 必须在 v1.0.0 提供教师推理能力
MUST：以下场景为本要求的强制验收条件。
TIAS 必须在 `/ImageDetect/teacher/v1.0.0` 提供教师推理能力，并返回帧级教师行为和头姿指标。

#### Scenario: 教师推理成功
- **WHEN** ai_quality 提交包含 `return_head_pose=true` 的教师帧
- **THEN** TIAS 必须为每帧返回一条结果，包含教师行为字段以及面向方向、是否低头、头姿是否有效等头姿字段

#### Scenario: 教师结果保留帧身份
- **WHEN** TIAS 返回教师推理结果
- **THEN** 每条结果必须包含 `frame_id`、`frame_index` 和 `timestamp_seconds`

### Requirement: TIAS 必须执行本地准入控制
MUST：以下场景为本要求的强制验收条件。
TIAS 必须在开始推理前执行本地准入控制，确保不会接收超过配置并发上限的运行中批次。

#### Scenario: 准入配置被加载
- **WHEN** TIAS 启动
- **THEN** 它必须从 `config.toml` 加载 `MaxConcurrentBatches` 和 `MaxQueueSize`

#### Scenario: 实例有可用容量
- **WHEN** `running_batches` 小于 `max_concurrent_batches`
- **THEN** TIAS 必须接收该批次，并在批次完成前增加 `running_batches`

#### Scenario: 实例满载且未开启本地队列
- **WHEN** `running_batches` 大于或等于 `max_concurrent_batches`，且 `max_queue_size` 为 `0`
- **THEN** TIAS 必须用可重试 busy 响应拒绝该批次

#### Scenario: 实例本地队列已满
- **WHEN** 本地队列已启用，且 `queued_batches` 大于或等于 `max_queue_size`
- **THEN** TIAS 必须用可重试 unavailable 响应拒绝该批次

### Requirement: TIAS 必须暴露真实 worker 状态
MUST：以下场景为本要求的强制验收条件。
TIAS 必须暴露 worker 状态接口，用于返回真实运行状态、队列状态、耗时指标和失败指标。

#### Scenario: worker 状态被查询
- **WHEN** ai_quality 调用 `/AE/WorkerStatus`
- **THEN** TIAS 必须返回 `instance_id`、`status`、`capabilities`、`max_concurrent_batches`、`running_batches`、`queued_batches`、`max_queue_size`、`available_slots`、耗时指标、成功数、失败数和最近错误摘要

### Requirement: TIAS 必须支持健康检查和排空操作
MUST：以下场景为本要求的强制验收条件。
TIAS 必须提供轻量健康检查和排空操作，用于受控发布和关闭。

#### Scenario: 健康检查成功
- **WHEN** 模型已加载且进程可以提供推理服务
- **THEN** `/AE/Health` 必须返回健康状态

#### Scenario: 排空模式启用
- **WHEN** 运维方调用排空接口
- **THEN** TIAS 必须停止接收新推理批次，并继续处理已经接收的批次

### Requirement: TIAS 必须输出简洁关键推理日志
MUST：以下场景为本要求的强制验收条件。
TIAS 必须用简洁中文记录服务和推理关键事件，并使用稳定字段便于检索。

#### Scenario: TIAS 服务启动
- **WHEN** TIAS 成功启动
- **THEN** 它必须记录 `instance_id`、`base_url`、`max_concurrent_batches` 和 `max_queue_size`

#### Scenario: TIAS 收到推理批次
- **WHEN** TIAS 收到学生或教师推理批次
- **THEN** 它必须记录 `request_id`、`task_id`、`batch_id`、帧数、`running_batches` 和 `queued_batches`

#### Scenario: TIAS 拒绝忙碌批次
- **WHEN** TIAS 因本地准入满载拒绝批次
- **THEN** 它必须记录 `task_id`、`batch_id`、`running_batches`、`max_concurrent_batches` 和 `max_queue_size`

#### Scenario: TIAS 批次完成或失败
- **WHEN** TIAS 完成或失败某个批次
- **THEN** 它必须记录耗时、成功帧数、失败帧数、错误阶段和错误原因

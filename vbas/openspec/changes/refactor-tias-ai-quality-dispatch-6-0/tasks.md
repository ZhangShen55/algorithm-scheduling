## 0. app 到 tias 的包名和目录迁移

- [x] 0.1 将当前 `app/` 推理服务目录迁移为 `tias/`，保留模型、vendor、routers、services、schemas、core 等内部结构
- [x] 0.2 将 TIAS 内部导入从 `app.*` 迁移为 `tias.*`，新增代码不得继续依赖 `app.*`
- [x] 0.3 将启动入口从 `uvicorn app.main:app` 迁移为 `uvicorn tias.main:app`
- [x] 0.4 更新 `Dockerfile`、`Dockerfile_cuda113`、`start.sh`、README、运行脚本和测试脚本中的 `app` 路径或包名
- [x] 0.5 将配置样例中的 `app/models`、`app/vendor/DirectMHP` 等默认路径迁移为 `tias/models`、`tias/vendor/DirectMHP`
- [x] 0.6 检查 `ai_quality` 代码，确保远程推理模式不直接 import `app.*` 或 `tias.services.*`
- [x] 0.7 迁移后使用 `rg "from app|import app|app.main|app/models|app/vendor"` 检查旧引用残留，并确认残留项均为历史说明或兼容说明

## 1. TIAS 6.0 接口裁剪与服务边界

- [x] 1.1 盘点并确认当前 TIAS 暴露接口，形成保留、废弃、兼容开关三类清单
- [x] 1.2 增加 TIAS 6.0 配置项，包括 `TiasExposeLegacySyncTasks`、实例 ID、服务根地址、`AiQualityBaseUrl`、`MaxConcurrentBatches`、`MaxQueueSize`
- [x] 1.3 新增或整理 TIAS 6.0 API 路由结构，区分推理接口、状态接口、运维接口和旧兼容接口
- [x] 1.4 将 `/AE/SyncTasks`、`/AE/SyncTasks2` 置于 `TiasExposeLegacySyncTasks` 开关后，课堂质量链路不再调用
- [x] 1.5 移除 `/AE/Capacity`、`/AE/Capacity_v2`、`/AE/Version`、`/AE/LogLevel` 路由
- [x] 1.6 将当前 `/ImageDetect/student/v1.0.1` 的并行实现迁移到 `/ImageDetect/student/v1.0.0`，并移除 `/ImageDetect/student/v1.0.1` 路由

## 2. TIAS 小批次推理接口

- [x] 2.1 定义学生推理请求和响应 schema，包含 task_id、batch_id、frame_id、frame_index、timestamp_seconds 和图像载荷字段
- [x] 2.2 定义教师小批次推理请求和响应 schema，包含 return_head_pose、教师行为、头姿和帧身份字段
- [x] 2.3 实现 `/ImageDetect/student/v1.0.0` 学生推理接口，复用当前并行学生行为模型能力并返回 ai_quality 所需计数字段
- [x] 2.4 实现教师小批次推理接口，复用现有教师行为和头姿能力并返回 ai_quality 所需计数字段
- [x] 2.5 增加批次结果完整性校验，保证每个请求帧都有对应成功或失败结果

## 3. TIAS 本地准入控制与状态采集

- [x] 3.1 实现 TIAS 本地推理准入控制器，维护 running_batches、queued_batches、max_queue_size、available_slots
- [x] 3.2 在推理接口入口增加准入检查，满载时返回可重试 busy 或 unavailable 响应
- [x] 3.3 在推理完成和异常路径确保释放 running_batches，避免计数泄漏
- [x] 3.4 采集平均耗时、P95 耗时、成功数、失败数和最近错误，第一版不采集 CPU、内存、GPU、显存指标
- [x] 3.5 新增 `/AE/WorkerStatus`，返回真实实例状态、队列状态、并发状态和耗时指标
- [x] 3.6 新增 `/AE/Health`，返回进程和模型是否可服务
- [x] 3.7 新增 `/AE/Drain`，支持实例进入 DRAINING 状态并停止接收新批次

## 4. ai_quality HTTP 入口、Redis 注册表与 TIAS 主动注册

- [x] 4.1 为 ai_quality 新增 FastAPI HTTP 入口，提供独立启动命令和配置项
- [x] 4.2 增加 Redis 连接配置和依赖，用于多 ai_quality 实例共享 TIAS 注册表
- [x] 4.3 定义 TIAS 注册请求、心跳请求、注销请求和响应 schema，包含 MaxConcurrentBatches 与 MaxQueueSize
- [x] 4.4 在 ai_quality 中新增 TIAS 注册接口，校验并写入 Redis：instance_id、base_url、capabilities 和并发能力
- [x] 4.5 在 ai_quality 中新增 TIAS 心跳接口，更新 Redis 中的状态、队列状态、耗时指标、TTL 和最近心跳时间
- [x] 4.6 在 ai_quality 中新增 TIAS 注销或下线接口，支持实例标记 DOWN 或移除可调度状态
- [x] 4.7 实现 Redis 共享实例注册表，包含实例集合 key、实例状态 key、TTL 过期和残留集合清理
- [x] 4.8 在 TIAS 启动后按 `AiQualityBaseUrl` 主动注册 ai_quality，并按配置周期发送心跳
- [x] 4.9 在 TIAS 优雅退出或 Drain 时上报 DRAINING/DOWN 状态

## 5. ai_quality 远程 TIAS 客户端与调度器

- [x] 5.1 增加 ai_quality 配置项，包括 TiasInferenceMode、TiasBatchSize、请求超时、重试次数、熔断阈值、静态兜底实例列表
- [x] 5.2 抽象 FrameAnalyzer 接口，保留本地实现并新增远程 TIAS 实现
- [x] 5.3 实现 TIAS HTTP 客户端，封装学生批次推理、教师批次推理、状态查询和错误分类
- [x] 5.4 实现实例健康过滤逻辑，过滤心跳过期、能力不匹配、状态不可用和熔断中的实例
- [x] 5.5 实现简单实例排序逻辑，按 running_batches、avg_latency_ms、p95_latency_ms、queued_batches、recent_failure_count 选择实例
- [x] 5.6 实现本地短期预留逻辑，降低同一 ai_quality 进程内并发批次选中同一 TIAS 的概率
- [x] 5.7 实现 retryable 错误换实例重试，参数错误不重试
- [x] 5.8 实现 TIAS 实例熔断和冷却恢复
- [x] 5.9 增加 ai_quality 关键日志，覆盖 Kafka 消费、批次生成、实例选择、TIAS 调用耗时、失败原因、落库结果和 offset 提交

## 6. ai_quality Kafka 任务小批次处理

- [x] 6.1 将课程视频抽帧结果转换为带 frame_id、frame_index、timestamp_seconds 的帧对象
- [x] 6.2 按 TiasBatchSize 将学生帧和教师帧切成小批次
- [x] 6.3 将学生小批次调度到具备 student_behavior 能力的 TIAS 实例
- [x] 6.4 将教师小批次调度到具备 teacher_behavior 和 teacher_head_pose 能力的 TIAS 实例
- [x] 6.5 按 stream_type 和 frame_index 合并批次结果，保证结果顺序稳定
- [x] 6.6 复用现有指标聚合、快照策略、学生行为统计和入库流程
- [x] 6.7 远程推理永久失败时只标记 `lesson_ai_workflow` 失败，并按现有策略提交 Kafka offset，不写 `lesson_ai_job`

## 7. 测试与联调

- [x] 7.1 为 TIAS 6.0 schema、准入控制、WorkerStatus、Health、Drain 增加单元测试
- [x] 7.2 为 TIAS 注册、心跳、注销和注册表过期逻辑增加单元测试
- [x] 7.3 为 ai_quality 调度器过滤、打分、预留、重试和熔断增加单元测试
- [x] 7.4 为远程 FrameAnalyzer 增加 HTTP mock 测试，覆盖学生和教师批次响应解析
- [x] 7.5 为 Kafka 任务处理增加多批次结果合并测试
- [x] 7.6 使用单个 TIAS 实例完成端到端联调
- [x] 7.7 使用多个 TIAS 实例完成负载均衡联调，验证批次分布、busy 重试和熔断恢复
- [x] 7.8 跑通 ai_quality 全量测试和 TIAS 相关全量测试
- [x] 7.9 本地无 Redis 时用 Docker 拉起 Redis，并验证 TIAS 注册、心跳、TTL 过期和恢复
- [x] 7.10 本地拉起 4 个 TIAS 实例，向 `10.67.65.8:9092` 的 `classroom_cv_task` 发送 6 个课程任务，验证多实例调度、落库、快照和 offset
- [x] 7.11 将 `10.80.5.131:/image` 挂载到项目 `mnt` 目录，验证快照文件写入、相对路径入库和 1/4 缩放策略
- [x] 7.12 验证测试期间 `ai_quality` 不插入、不更新、不改写 `lesson_ai_job`

## 8. 日志、配置样例与迁移文档

- [x] 8.1 更新 README 或部署文档，说明 ai_quality 与 TIAS 独立启动方式
- [x] 8.2 补充 TIAS 6.0 接口清单、废弃接口清单和兼容开关说明
- [x] 8.3 补充 ai_quality 调度配置示例和字段中文注释
- [x] 8.4 补充故障处理说明，包括 TIAS 满载、心跳过期、Kafka offset 提交时机、Kafka 失败任务处理
- [x] 8.5 明确生产部署建议：一个 TIAS 实例对应一个端口和一个推理准入队列
- [x] 8.6 新增 ai_quality 的 `config.toml.example`，包含 HTTP、Redis、TIAS 批大小、心跳超时、调度重试和 Kafka 配置
- [x] 8.7 新增 tias 的 `config.toml.example`，包含服务目录、进程、GPU_ID、AiQualityBaseUrl、TiasExposeLegacySyncTasks、MaxConcurrentBatches、MaxQueueSize、心跳、模型阈值和教师头姿配置
- [x] 8.8 增加 TIAS 关键日志，覆盖启动、注册、心跳失败、收到批次、准入拒绝、批次耗时和失败原因
- [x] 8.9 检查新增代码注释和日志文案，确保简洁中文、无 emoji、无 AI 风格冗余表达

## 背景

当前 `ai_quality` 与当前 `app/` 推理服务仍存在代码级耦合，`ai_quality` 直接调用 `app.services.*` 完成本地推理，无法把多个 TIAS 推理服务作为独立资源池进行调度。进入 6.0 后需要明确 `ai_quality` 与 `tias` 是两个独立服务，并为多 TIAS 实例提供可观测、可注册、可心跳、可负载均衡的稳定调度机制。

同时，现有 TIAS 接口中包含面向旧 IAS 协议或调试用途的接口，部分接口的状态值是示例值，不能作为调度依据。需要裁剪 TIAS 6.0 对外接口，保留课堂质量视觉分析所需的推理能力和运维能力，并新增真实运行状态接口。

## 变更内容

- **BREAKING**：`ai_quality` 不再直接 import 或调用 `app.services.*`，改为通过 HTTP 调用独立的 `tias` 推理服务。
- **BREAKING**：当前 `app/` 目录在 6.0 中迁移为 `tias/` 包和服务目录；启动入口、Dockerfile、脚本、测试和文档中的 `app.*` 引用需要同步迁移为 `tias.*`。
- **BREAKING**：`tias` 对外职责收敛为模型推理、实例状态、注册心跳和运维检查，不再作为泛化的 `app` 服务存在。
- **BREAKING**：TIAS 6.0 移除不能反映真实资源状态的 `/AE/Capacity`、`/AE/Capacity_v2`，移除旧串行学生接口实现，并移除 `/AE/Version`、`/AE/LogLevel`。
- 保留 `/AE/SyncTasks`、`/AE/SyncTasks2` 的代码能力，但通过配置控制是否暴露接口地址；课堂质量链路不调用这两个接口。
- 保留并规范 TIAS 推理接口：学生行为接口统一使用 `/ImageDetect/student/v1.0.0`，其实现采用当前 `/ImageDetect/student/v1.0.1` 的并行模型逻辑；教师行为/头姿接口保留 `/ImageDetect/teacher/v1.0.0`。
- 新增 TIAS 真实状态接口，第一版只返回实例状态、处理中批次、排队批次、最大并发、最大队列、平均耗时、P95 耗时、失败计数、能力清单等调度必要字段，不采集 CPU、内存、GPU、显存指标。
- 新增 TIAS 主动注册和心跳机制：TIAS 启动后向 `ai_quality` 注册实例，周期性上报状态；`ai_quality` 维护实例注册表和健康状态。
- 新增 `ai_quality` FastAPI HTTP 入口，用于接收 TIAS 注册、心跳和注销请求；Kafka worker 与 HTTP 入口通过 Redis 共享 TIAS 注册表。
- 新增 `ai_quality` 调度器：在收到 Kafka 生产消息后抽帧并按小批次调度到多个 TIAS 实例。
- 新增小批次级负载均衡策略：基于实例能力、健康状态、`max_concurrent_batches`、`running_batches`、`queued_batches`、平均耗时、P95 耗时和近期失败进行简单选择。
- 新增 TIAS 本地准入控制：即使 `ai_quality` 误判空闲，TIAS 也必须在满载时拒绝请求，并返回可重试错误。
- `max_concurrent_batches` 和 `max_queue_size` 必须释放到 `config.toml`；`max_queue_size = 0` 表示不开启 TIAS 本地排队，满载直接返回可重试忙碌。
- 在设计文档中补充完整 Mermaid DSL 时序图，并为关键请求/响应字段提供中文注释。

## 能力范围

### 新增能力

- `tias-inference-service`: 定义 TIAS 6.0 独立推理服务的接口裁剪、保留接口、新增状态接口、本地并发准入和队列状态上报要求。
- `tias-ai-quality-registration`: 定义 TIAS 主动向 `ai_quality` 注册、心跳续约、实例下线、健康状态维护和字段语义。
- `ai-quality-tias-dispatch`: 定义 `ai_quality` 在收到 Kafka 任务后，如何抽帧、切小批次、选择 TIAS 实例、重试、熔断、合并结果并完成课堂质量指标入库。

### 修改能力

- 无。

## 影响范围

- 影响 `ai_quality`：Kafka worker、视频抽帧、FrameAnalyzer 抽象、远程 TIAS 客户端、调度器、配置项、任务重试和测试。
- 影响 `tias`：由当前 `app/` 迁移而来，涉及包名、目录名、启动入口、接口路由、推理接口版本、实例状态采集、注册心跳客户端、本地队列和并发控制。
- 影响部署：`ai_quality` 与 `tias` 作为独立服务部署；多个 TIAS 实例需要可被 `ai_quality` 访问；建议一个 TIAS 实例对应一个端口和一个推理准入队列。
- 影响依赖：新增 Redis 作为多 `ai_quality` 实例共享的 TIAS 实时注册表。
- 影响配置：新增 `ai_quality` HTTP 服务配置、Redis 连接配置、TIAS 静态兜底实例列表、心跳间隔、实例超时、批大小、重试次数、熔断阈值、`max_concurrent_batches`、`max_queue_size`、`AiQualityBaseUrl` 等配置。
- 影响接口兼容：旧 IAS 同步任务接口若仍有外部依赖，需要通过配置开关暴露；课堂质量链路不依赖这些旧接口。旧 Capacity、Version、LogLevel 接口进入移除范围。
- 影响工程引用：`uvicorn app.main:app`、`from app...`、`app/models`、`app/vendor`、`app/config.toml` 等路径需要迁移为 `tias` 对应路径；短期兼容只能作为迁移辅助，不作为 6.0 正式依赖。

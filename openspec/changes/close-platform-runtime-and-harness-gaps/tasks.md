## 1. Harness 与持久化 Agent 指南

- [x] 1.1 将 `algorithm-scheduling-platform` 加入根 `AGENTS.md` 项目地图，并规定 VBas 只保留帧级推理的跨项目边界
- [x] 1.2 建立 `algorithm-scheduling-platform/AGENTS.md`，记录四服务所有权、稳定契约、字段名、依赖归属、禁止捷径和验证等级
- [x] 1.3 建立 `harness/README.md`、架构证据矩阵、变更台账、验证命令和场景模板
- [x] 1.4 记录 Worker 仅健康入口、合成验收、Kafka adapter 缺失、清理/指标/审计未接线、Compose 和算子 wheel 缺口的基线
- [x] 1.5 增加 Harness 一致性测试，确保每项架构决策都有负责人、证据命令、结论和关联场景

## 2. 配置、数据库与 Kafka 基础

- [x] 2.1 为四服务增加带中文注释的 `config.toml` 和类型化配置，覆盖基础设施、主题、消费组、并发、关闭、媒体、PPT 共享结果、租约和就绪探针
- [x] 2.2 增加 `0004_schema_comments.sql`，为 10 张调度表及全部物理字段写入中文说明，并记录本机 PostgreSQL 只读审计结果
- [x] 2.3 结合目标 Python 与算法环境的 wheel 兼容性选择正式 Kafka 客户端，并在 Harness 记录决策
- [x] 2.4 实现共享异步 Kafka Producer/Consumer adapter，支持启动、停止、确认发送、手动提交、有界轮询和 lag 指标
- [x] 2.5 增加 `algorithm.course.commands`、`algorithm.visual.commands` 和 `algorithm.visual.events` 的 topic 引导与校验
- [x] 2.6 增加真实 Broker 的发布、消费、手动提交、重连、重复投递和 Broker 不可用就绪测试

## 3. 方案 C 里程碑 1：control-service 事实闭环

- [x] 3.1 将 PostgreSQL Repository 装配到真实 `control-service` lifespan，验证任务幂等提交、任务类型追加和完整查询
- [x] 3.2 验证课程事实、任务类型与 Outbox 在同一事务中提交，control API 不直接发布 Kafka
- [x] 3.3 增加算子注册声明、心跳摘要、生命周期变化、注销事件和运维历史的 PostgreSQL Repository
- [x] 3.4 封装 Redis 注册中心，使 TTL/租约实时态留在 Redis，重要审计事实事务写入 PostgreSQL
- [x] 3.5 使用真实 PostgreSQL/Redis 验证整数状态、中文 `reason`、URGENT/NORMAL、注册、排空和容量租约

## 4. 方案 C 里程碑 2：orchestrator-service 通用运行时

- [x] 4.1 用 lifespan 管理的运行时工厂替换仅健康检查的 orchestrator 入口，统一持有 engine、HTTP client、Kafka、停止事件和后台任务组
- [x] 4.2 将 Outbox Publisher 接入真实 Kafka Producer，验证发布失败时事件保持待发布并在 Broker 恢复后继续
- [x] 4.3 将课程命令 Consumer 接入 `PipelineInitializer`，仅在幂等 DAG 初始化成功后提交 offset
- [x] 4.4 实现 Dispatcher 循环，按 URGENT 优先于 NORMAL 领取等待节点、不抢占运行节点，并在无容量时暴露状态 30
- [x] 4.5 实现通用算子调用框架、执行上下文、容量租约、状态推进、任务类型汇总、就绪检查和优雅停止
- [x] 4.6 建立不依赖真实 PPT 的通用 HTTP 契约 Stub，验证 Stub 注册、选择、调用和结构化结果持久化
- [ ] 4.7 实现按 `submission_id` 隔离的执行上下文和共享下载协调，使同一次 ASR/教师组合提交只共享一次 T 下载
- [ ] 4.8 等 PPT 契约冻结后接入共享路径切片、原子 manifest、幂等终态通知、manifest 对账、容量续约和 OCR 释放
- [ ] 4.9 实现 `PPT_OCR` 与 `PPT_KEYWORDS`，按 `ppt_image_id` 子项、配置并发、租约和部分进度持久化
- [ ] 4.10 实现 `ASR_TRANSCRIPTION` 的媒体/WAV/租约执行，校验 v1.1.8 业务响应并持久化完整结果
- [ ] 4.11 从已保存 ASR segments 执行 `COURSE_OVERVIEW`，持久化完整嵌套 GenericResponse
- [ ] 4.12 将教师/学生视觉节点发布到 `algorithm.visual.commands`，并幂等消费视觉进度/完成事件
- [x] 4.13 从节点状态推导任务类型状态和当前节点中文原因，禁止测试或算子直接更新任务终态
- [x] 4.14 增加 orchestrator 就绪和关闭测试，证明必需循环启动、异常可见、停止消费并关闭资源

## 5. vision-orchestrator-service 运行时闭环

- [ ] 5.1 用 lifespan 管理的课程级视觉 Consumer 与进度/完成 Producer 替换仅健康检查入口
- [ ] 5.2 实现安全的 T/S 本地抽帧、时间戳标识、可配置粗扫/细化计划、限制和 `/data/course/{task_id}` 所有权
- [ ] 5.3 实现具体 `VisualAnalyzer`，组合缓存、`AdaptiveScanPlanner`、容量路由的 `VbasBatchClient`、教师区间和学生聚合
- [ ] 5.4 接入板书/坐姿缺口容忍、有效帧不足原因、空完成区间、前后排 provided 标识和稳定 PostgreSQL 兜底值
- [ ] 5.5 将精选证据发布到 `/data/result/{task_id}/vision`，普通抽帧继续作为临时文件
- [ ] 5.6 使用 HTTP VBas 契约服务增加教师、学生、细化、矛盾帧、无行为和图像不足的确定性集成测试
- [ ] 5.7 增加真实 Kafka 视觉命令/进度/完成、重启和幂等测试

## 6. 清理、可观测性与在线资源

- [ ] 6.1 将任务/节点状态、Kafka lag、算子就绪、活跃租约、Outbox 积压、延迟、错误、GPU 标签和磁盘使用接入真实运行快照
- [ ] 6.2 在领取、开始、算子结果、失败和完成边界写结构化节点审计日志，包含任务、节点、尝试、trace、实例、模型、耗时和结果
- [ ] 6.3 在任务终态调用 `TerminalWorkspaceCleaner`，记录成功、延迟和错误，不删除 `/data/result/{task_id}`
- [ ] 6.4 修复 `/ops/queues` 指标，使其标记真实节点代码而非能力名，并增加运维快照测试
- [ ] 6.5 在 online gateway lifespan 中关闭共享 HTTP 资源，并增加关闭回归测试

## 7. 可复现的单机部署

- [x] 7.1 将四个平台服务整理为完整 FastAPI 项目，提供 `app`、注释配置、依赖、Dockerfile，并增加含重启、就绪、共享挂载、资源和网络的 Compose
- [x] 7.2 增加 Kafka 主机/internal listener，并说明宿主机进程和容器分别使用的 bootstrap 地址
- [x] 7.3 构建版本化平台 wheel，并更新八个算子镜像安装它，不依赖源码挂载或临时 `PYTHONPATH`
- [x] 7.4 增加隔离镜像测试，验证导入 `packages.operator_registry_client`、启用注册后启动且保持业务路由和默认端口
- [x] 7.5 增加部署预检，覆盖 `/data/course` 可写、`/data/result` 持久、GPU 标签、唯一实例 ID、数据库迁移、topics 和端口

## 8. 方案 C 基础闭环验收

- [x] 8.1 启动真实 PostgreSQL、Redis、Kafka、control、orchestrator 和通用契约 Stub，贯通 `POST -> Outbox -> Kafka -> DAG -> Stub -> GET`
- [x] 8.2 验证查询状态完全由运行中的 Worker 产生；若测试直接调用 Repository 完成节点，Harness 必须失败
- [x] 8.3 验证 URGENT 插队、无算子状态 30、算子恢复、重复 Kafka 消息、Publisher 重启、Worker 重启和 offset 恢复
- [x] 8.4 记录容器状态、版本、API 证据、Outbox 行、topic offset、节点变化、实例选择、Redis 租约和最终结果
- [x] 8.5 在不部署真实 PPT、ASR、视觉和在线算子的条件下完成基础闭环验收，并明确不得扩张为完整产品完成声明

## 8A. 里程碑 2B 真实泳道分期

- [x] 8A.1 将 217 条反例和 26 条压力用例固化为严格结构化目录，验证 243 个稳定 ID、分类、阶段、runner、超时和安全级别
- [x] 8A.2 实现兼容历史声明的真实执行证据合同、安全有界 case runner 及部署/GPU/注册/基础设施执行器
- [x] 8A.3 在新 Git SHA 和不可变 release 下重跑 FaceRec 三实例、18 个 GPU 实例和部署阶段用例
- [ ] 8A.4 贯通 PPT/OCR/关键词和离线 ASR/课程脑图，执行对应反例和压力用例
- [ ] 8A.5 贯通课程级视觉命令、抽帧、自适应 VBas、聚合、证据和完成事件，执行对应用例
- [ ] 8A.6 贯通在线图片、实时 ASR WebSocket 和 FaceRec 人物管理代理，执行对应用例
- [ ] 8A.7 在同一新 release 中重新执行全部 217 条反例和 26 条压力用例，最终报告不允许失败或“未执行及原因”

## 9. 完整产品端到端验收

- [ ] 9.1 建立契约兼容的 PPT、OCR、文本分析、离线/实时 ASR、VBas、人脸和图像质量 HTTP/WebSocket 算子替身
- [ ] 9.2 使用真实 PostgreSQL、Redis、Kafka、四平台服务和算子替身运行 PPT-only、ASR-only、teacher-only、student-only 和组合请求
- [ ] 9.3 验证同任务完成结果复用、后续任务类型追加、同提交 T 下载复用、后续提交重新下载和准确 `effective_params`
- [ ] 9.4 验证视觉细化、空行为、图像不足、稳定区域兜底、证据留存和终态临时文件清理
- [ ] 9.5 验证在线图片不进入 Kafka/媒体下载、完整请求不拆分，实时 ASR 保持粘性且与离线 ASR 分离
- [ ] 9.6 在 Harness 记录全部命令、环境版本、容器状态、topic offset、API、指标、文件系统证据和最终结论

## 10. 最终架构复审与交接

- [ ] 10.1 重跑设计到实现证据矩阵，解决每个“不符合”项，或通过批准的规格更新明确重新划定范围
- [x] 10.2 维护 `docs/算法功能调度平台总体设计-v2.md` 及视觉检查后的 PDF，并同步 README、A 服务指南、运行手册、部署命令和图；旧离线文档只作历史基线
- [ ] 10.3 运行 lint、严格类型检查、单元、契约、PostgreSQL/Redis 集成、真实 Kafka、镜像构建、Compose 和全部 Harness 场景
- [ ] 10.4 记录最终符合性结论和剩余非目标，再决定是否同步或归档原变更与本变更

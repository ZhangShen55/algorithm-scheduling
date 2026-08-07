## 为什么

现有 A 服务承担三路课程视频的媒体处理与算法串联，而现有 ASR、PPT 切片、OCR、文本分析、VBas、人脸识别和图像质量检测均以独立同步服务运行，缺少统一的异步任务状态、节点编排、多实例容量路由、文件生命周期和运维视图。现在需要建设一套不依赖 Kubernetes、可在单机 Docker 环境运行的算法调度平台，接管课后离线处理，并为在线图片与实时语音提供统一实例分发能力。

## 变更内容

- 新建包含 `control-service`、`orchestrator-service`、`vision-orchestrator-service`、`online-gateway-service` 的算法调度平台。
- 提供按唯一 `task_id` 提交和查询课程任务的北向接口，允许同一课程分多次追加 `PPT`、`ASR`、`TEACHER_BEHAVIOR`、`STUDENT_BEHAVIOR` 任务。
- 使用 PostgreSQL 事务型 Outbox 与 Kafka 可靠触发离线任务，使用 Redis 保存算子运行态和容量租约。
- 将离线任务展开为 PPT 切片/OCR/关键词、离线 ASR/课程脑图、教师行为、学生行为四类业务管道，并支持 `URGENT`、`NORMAL` 非抢占式优先级。
- 增加算子主动注册、心跳、注销、排空、健康状态和容量感知路由；一个 Docker 端点视为一个实例，ASR 每个 GPU/端口以 `workers=1` 独立注册。
- 增加在线图片与实时 ASR 网关：Base64 图片按完整请求选实例，实时 WebSocket 按会话粘性选实例，二者不进入 Kafka。
- 将视觉自适应抽帧与 VBas 帧级推理解耦，通过粗扫和逐级加密检测聚合教师行为区间、学生人数与行为统计。
- 统一临时文件与长期结果文件边界：`/data/course/{task_id}` 可清理，`/data/result/{task_id}` 长期保留；结构化结果写 PostgreSQL 并通过节点 `result` 返回。
- 保持现有算子接口协议，由平台适配器调用真实接口；ASR v1.1.8、课程脑图、OCR、关键词等结果不由平台重新发明格式。
- **BREAKING**：平台层彻底使用 `vbas` 命名，不保留旧 `tias` 标识、路径或注册代码；平台北向接口统一使用 `/api` 前缀，不使用 `/v1` 前缀。

## 能力范围

### 新增能力

- `course-job-lifecycle`: 课程任务提交、按任务类型幂等追加、整数状态机、全任务与节点结果查询。
- `offline-pipeline-orchestration`: Outbox、Kafka、DAG、节点执行、两级优先级和四类离线业务管道。
- `operator-instance-management`: 算子注册、心跳、排空、健康检查、容量租约和多实例选择。
- `online-inference-routing`: 三个在线图片接口的请求级路由及实时 ASR WebSocket 的会话级粘性路由。
- `adaptive-vision-analysis`: T/S 视频抽帧、VBas 多轮检测、行为区间细化、人数/行为聚合与证据快照。
- `result-media-lifecycle`: 结构化结果持久化、节点结果映射、长期文件发布及临时工作区清理。

### 调整能力

无。当前主规格目录为空，本次全部作为新能力建立。

## 影响范围

- 新增调度平台仓库结构及四个独立可部署服务。
- 影响 A 服务对接的课程任务提交、查询、在线图片和实时 ASR 入口。
- 影响现有算法服务的平台注册客户端、健康检查和单实例部署约定，但不改变其既有推理协议。
- 新增 PostgreSQL、Kafka、Redis、本地共享目录及对应部署配置。
- 需要适配 `asr_offline`、`asr_online`、`ppt_slice`、`ocr`、`text_analysis`、`vbas`、`facerec`、`screen_det`。
- 需要将现有外部 `jy-vision-orchestrator-server` 演进为目标 `vision-orchestrator-service` 边界，并统一使用 PostgreSQL 与平台实例管理协议。

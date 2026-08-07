## 背景

当前工作区包含八个独立算法项目：`asr_online`、`asr_offline`、`facerec`、`ocr`、`screen_det`、`ppt_slice`、`vbas`、`text_analysis`。它们主要通过同步 HTTP 或 WebSocket 提供推理能力，以 Docker 单独部署；同一算法可能在不同 GPU 和端口运行多个实例。当前没有 Kubernetes，也没有统一的课程任务状态、依赖编排、容量路由和文件生命周期。

上游 A 服务能够提供同一节课的 T 教师视频、S 学生视频和 P 课件录屏 URL，但一次请求只会选择所需业务任务，不保证三路字段全部存在。PPT、ASR、教师行为和学生行为可在同一 `task_id` 下分多次追加。在线 VBas、人脸识别、图像质量请求由 A 直接携带 Base64 图片；实时 ASR 使用 WebSocket，二者均不进入课后离线 DAG。

第一阶段在一台服务器上部署全部平台服务、算法容器、PostgreSQL、Kafka 和 Redis，共享 `/data/course` 与 `/data/result`。离线并发预期 10-100，允许异步完成。A 只调用平台 API，不直连平台数据库。

## 目标 / 非目标

**Goals:**

- 建立可持久化、可查询、可分次追加的课程任务与节点状态。
- 通过四个职责清晰的服务支持离线课程、在线图片和实时语音。
- 通过 Outbox + Kafka 保证离线任务可靠进入执行流程。
- 通过算子注册、心跳和 Redis 容量租约路由多个 Docker 实例。
- 复用现有算法协议并保存真实结果格式。
- 支持视觉粗扫、多轮加密、区间合并和证据快照。
- 保证临时媒体清理不会删除长期业务结果。

**Non-Goals:**

- 第一版不引入 Kubernetes、Service Mesh 或通用工作流产品。
- 不做三路视频轻微起始偏差校正。
- 不让在线网关接入 RTSP、拉流或截图。
- 不在 Kafka 中传视频、音频或图片二进制。
- 不在本变更中确定完整 PostgreSQL 表结构、失败重试策略、取消/补跑和管理前端。
- 不重构现有算法模型逻辑，也不改变既有推理接口。

## 设计决策

### 1. 四个可部署服务

```text
control-service
├── A-facing 课程任务提交与查询
├── 任务状态和 Outbox 事务写入
└── 通用算子注册、心跳、租约与运维查询

orchestrator-service
├── Outbox Publisher
├── Kafka Consumer 与课程 DAG
├── 媒体准备、节点执行和优先级
└── PPT/ASR 等算子适配器

vision-orchestrator-service
├── T/S 动态抽帧与缓存
├── 多轮 VBas 调用
└── 行为区间、人数指标和快照聚合

online-gateway-service
├── 三个 Base64 图片接口
└── 实时 ASR WebSocket 会话代理
```

选择四服务是因为视觉多轮分析具有独立的长任务生命周期，在线请求又需要低延迟且不能被离线 Kafka 消费阻塞。备选方案是全部合并为一个服务，部署更少但故障域、伸缩维度和事件循环相互影响；另一备选是把每个适配器拆成独立服务，会过早增加部署复杂度。

### 2. 同仓库、多入口部署

目标平台使用一个仓库：

```text
algorithm-scheduling-platform/
├── services/
│   ├── control_service/
│   ├── orchestrator_service/
│   ├── vision_orchestrator_service/
│   └── online_gateway_service/
├── packages/
│   ├── platform_common/
│   ├── platform_contracts/
│   └── operator_registry_client/
├── migrations/
├── deploy/
└── tests/
```

共享契约和基础代码，四个进程分别部署和扩缩。备选多仓库方案隔离更强，但单人开发阶段会产生重复发布和契约同步负担。

### 3. 课程与业务管道分离

`task_id` 是课程业务标识，唯一业务管道键为 `(task_id, task_type)`。任务类型限定为：

```text
PPT
ASR
TEACHER_BEHAVIOR
STUDENT_BEHAVIOR
```

任意类型可以首先到达。提交时仅校验被选类型所需字段，避免未使用字段影响健壮性。完成管道直接复用，运行中管道返回状态，不存在管道异步创建。

备选方案是每次请求创建新 `course_job_id`，但会让同一课程的结果难以自然聚合；另一方案是强制第一次必须提交 PPT，与 A 的实际调用顺序不符。

### 4. PostgreSQL + Outbox + Kafka

`control-service` 在一个事务中保存业务请求和 Outbox。`orchestrator-service` 内的 Publisher 发布后标记事件状态，Consumer 幂等创建节点。Kafka 负责可靠唤醒和服务解耦，不负责业务优先级排序；节点领取由 PostgreSQL 状态和调度器决定。

备选“写库后直接发 Kafka”存在崩溃窗口；仅使用数据库轮询也可行，但视觉独立服务和后续事件观测会受限。

### 5. Orchestrator 与视觉服务使用 Kafka，视觉与 VBas 使用 HTTP

课程级视觉任务耗时长、需要进度和完成事件，适合 Kafka。视觉服务内部每轮结果决定下一轮抽帧点，必须同步得到帧级结果，因此直接使用 HTTP 调用 VBas。Kafka 只携带 ID、任务类型、本地路径和策略元数据。

将每帧都发 Kafka 的备选方案会使迭代状态分散、消息量显著增加且不利于边界搜索。全程 HTTP 又会让课程 orchestrator 长时间占用连接并承担视觉内部细节。

### 6. 注册中心归 control-service

算子向 `control-service` 注册，不向适配器注册。Redis 保存带 TTL 的心跳与原子容量租约，PostgreSQL保存需要审计的实例元数据和运维事实。适配器按 `operator_code`、capability、状态和容量选实例。

注册 API：

```text
POST /api/operator-instances/register
POST /api/operator-instances/heartbeat
POST /api/operator-instances/unregister
GET  /api/operator-instances
POST /internal/operator-instances/lease
POST /internal/operator-instances/release
```

算子统一提供 `/ops/health`、`/ops/status`、`/ops/drain`。`ONLINE` 可接新任务，`DRAINING` 只完成存量，`OFFLINE` 不参与选择。

### 7. 一个端点是一个实例

一个容器/进程/端口/GPU 是一个平台注册实例。ASR 不使用容器内 Nginx 或多个 Uvicorn worker 伪装多实例；GPU0/GPU1 各自部署 `asr_offline` 和 `asr_online`，每个端点 `workers=1`、端口不同。离线调用按请求租约，实时 ASR 按 WebSocket 会话粘性租约。

这使容量、GPU 归属、故障和排空均可观测。进程恢复交给 Docker restart policy。

### 8. 在线请求不进入 Kafka

在线接口统一在 `online-gateway-service`：

```text
POST /api/online/vbas/analyze
POST /api/online/face/recognize
POST /api/online/image-quality/detect
WebSocket /api/online/asr/stream
```

上游提供 Base64 图片，不存在流到截图步骤。一个完整图片请求只选一个实例，不在请求内拆图；并发请求可以进入不同实例。实时 ASR 建连时选实例并保持会话粘性。

### 9. 节点状态使用整数

```text
0  未请求
10 待处理
20 等待前置节点
30 等待算子
40 已排队
50 处理中
60 已完成
70 处理失败
80 已取消
```

每个状态同时返回 `status_text` 和中文 `reason`。A-facing API 使用 HTTP 200 + `code/message/data`；内部基础设施接口使用真实 HTTP 状态，便于客户端和监控识别 400/429/503。

### 10. 四条离线业务管道

```text
PPT:              PPT_SLICE -> PPT_OCR -> PPT_KEYWORDS
ASR:              ASR_TRANSCRIPTION -> COURSE_OVERVIEW
TEACHER_BEHAVIOR: T 动态抽帧 -> VBas -> 行为区间/证据聚合
STUDENT_BEHAVIOR: S 动态抽帧 -> VBas -> 人数/行为/区域聚合
```

同一请求选择 ASR 和教师行为时共享一次 T 下载；平台在每次 POST 内部生成 `submission_id`，
通过 Outbox/Kafka 元数据向下传递，并以该标识隔离下载目录和并发锁。以后分开追加会得到新的
`submission_id` 并重新下载，不要求 A 传入该字段，也不为潜在未来任务保存视频或 WAV。

### 11. ASR 参数与真实结果

平台默认参数：

```json
{
  "language": "auto",
  "showSpk": true,
  "showEmotion": true,
  "showRoleIdentify": false,
  "wordTimestamps": false,
  "hotWords": []
}
```

A 可在 `asr_options` 中覆盖。`effective_params` 保存首次实际执行时的合并结果；已完成结果不因后续参数不同而重跑或产生版本。`wordTimestamps` 保留但不建议开启。

`ASR_TRANSCRIPTION.result` 原样保存 v1.1.8 成功响应。适配器必须识别 HTTP 200 中的 `code/msg` 错误体。`COURSE_OVERVIEW.result` 原样保存现有 `GenericResponse`，允许出现平台 `result` 内嵌算法 `result`。

### 12. 文件结果与结构化结果分离

```text
/data/course/{task_id}   下载视频、WAV、普通帧；终态后可删除
/data/result/{task_id}   PPT 切片、精选视觉证据；长期保留
```

只有真实文件使用 `path/count`。OCR、关键词、ASR、课程脑图、行为区间和人数统计保存在 PostgreSQL，通过节点 `result` 返回。`path` 是服务器本地/共享挂载路径，不是 URL。

### 13. 自适应视觉分析

视觉服务先按可配置间隔粗扫全课，再对候选行为向两侧扩展并使用如 10/5/2/1 秒间隔逐级细化边界。板书缺口默认不超过 3 秒合并，坐姿缺口默认不超过 5 秒合并。检测点按课程、流、时间戳、能力、模型版本和 ROI 版本缓存。

教师姿态 `STANDING/SITTING` 对有效检测点二选一；无效画面不强制填充。板书、坐姿和讲授保留代表帧。未检测到行为是 `status=60` + 空数组；有效画面不足与确认不存在使用不同中文原因。

### 14. 前后排区域与兜底

存在 `front_points` 或 `back_point` 时，分别计算稳定人数/识别总人数。缺失区域时，从 config 的对应最小/最大范围首次生成一个值并持久化，同一 `task_id` 后续稳定返回。响应包含 `front_region_provided`、`back_region_provided`，不增加 `is_estimated` 或 `source`。

### 15. 不在第一版定义完整数据库表

本变更先固定数据所有权、状态、事务边界和查询契约。具体表、索引、JSONB 拆分、分区与保留周期在数据库设计任务中完成，避免在业务契约尚未实现时过早锁定物理模型。

## 风险与权衡

- [单机是共享故障域] → 使用独立容器、健康检查、磁盘告警和可恢复 Outbox；容量增长后再评估多机或 Kubernetes。
- [本地 `path` 对 A 不一定可访问] → 明确 path 语义；若 A 不共享挂载，后续增加受控文件下载接口或对象存储，不把 path 伪装成 URL。
- [A-facing 全部 HTTP 200 降低通用可观测性] → 强制稳定业务码并在网关日志/指标中按业务码统计；内部接口保留真实 HTTP 状态。
- [ASR 结果很大] → 默认关闭逐词时间戳，数据库保存完整结果；达到实际规模后再设计结果分页或摘要查询。
- [同一 ASR 后续参数不同仍复用旧结果] → 返回原 `effective_params`，让调用方知道结果来源；需要重跑时另行设计显式版本/补跑。
- [粗扫可能漏掉完全位于采样点之间的短行为] → 粗扫间隔配置化并记录覆盖质量；不能把自适应细化误认为能发现所有短事件。
- [缺少前后排区域时的兜底值不是模型观测] → 通过 provided 布尔字段明确区域是否由上游提供，并保证一次生成后稳定，不在查询时抖动。
- [视觉迭代可能产生过多帧] → 配置最大轮次、最大检测点、批次大小、并发和缓存去重。
- [算法实例协议不一致] → 适配器隔离差异，注册契约只统一运行面，不强迫一次性重写所有算法业务接口。

## 迁移计划

1. 建立平台仓库骨架、共享契约、配置、日志、数据库迁移和本机 Docker 基础设施。
2. 实现 `control-service` 的课程提交/查询、状态模型、Outbox 和算子注册/租约。
3. 实现 `orchestrator-service` Publisher、Consumer、优先级和最小 PPT 管道，验证端到端任务闭环。
4. 接入 OCR、关键词、离线 ASR和课程脑图，按真实接口保存结果。
5. 将外部视觉项目适配为 `vision-orchestrator-service`，接入 Kafka 命令/事件和平台 VBas 租约。
6. 实现 `online-gateway-service` 三个图片接口，再实现实时 ASR 会话代理。
7. 为各算法增加注册客户端与 ops 端点；ASR 按一端点一实例迁移。
8. 增加运维查询、指标、磁盘清理和部署文档，完成单机联合验收。

回滚按服务分阶段进行：新平台接口在 A 切换前与旧链路并行验证；某一业务管道失败时 A 可暂时切回旧 A 编排。数据库迁移采用向前兼容脚本，Outbox 和任务数据不在回滚时删除。

## 待确认问题

- PostgreSQL 的物理表、JSONB 边界、索引和数据保留周期。
- A 服务最终补充的课程业务字段以及 URL 鉴权、有效期和下载安全策略。
- 结构化大结果是否在数据量验证后增加分页、摘要或独立结果接口。
- 失败重试、取消、人工补跑和强制重算的最终产品规则。
- 运维页面第一版的具体展示范围和鉴权方式。

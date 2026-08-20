## 背景

平台已经具备算子注册、5 秒心跳、Redis 原子租约、离线节点调度和在线同步路由，但当前容量事实存在三处混淆：

1. `operator_registry_client` 仍从多个 `PLATFORM_*` 环境变量读取注册开关、Control Service 地址、心跳和容量，八个算子没有统一的平台 TOML 配置；GPU 强制检查也由 `REQUIRE_GPU` 单独注入，PPT Slice 和 VBas 又直接复用了本地执行限制。
2. Redis 分配器用 `max(active_leases, reported_inflight)` 计算已用容量。短请求释放租约后，上一轮心跳仍可能保留较高 `reported_inflight`，造成最多约一个心跳周期的假满载。
3. 通用离线调度器按整个节点持有一个租约，PPT OCR 和关键词节点却在一个节点内扇出多张图片，导致租约数量不能表达真正的算子请求；现有租约也没有任务、节点和图片身份。

本变更涉及八个独立算子、公共注册包、Control Service、两个离线编排服务、Online Gateway、部署定义和 Harness。必须保持既有算子 HTTP/WebSocket 契约、默认端口、PPT 共享路径与结果持久化流程稳定。

## 目标 / 非目标

**目标：**

- 用统一 `[platform]` 表达八个算子的注册开关、Control Service 地址、心跳和每实例平台声明容量，并用 `[runtime].require_gpu` 表达 GPU 强制检查，给出确定默认值和启动期校验。
- 让 Control Service 成为平台分发容量的唯一权威，以 Redis 活跃租约而不是心跳采样控制是否还能分发。
- 让一个真实算子工作单元恰好对应一个租约，并能从 Control Service 查询该租约当前归属的任务、节点和工作项。
- 让在线 OCR 与离线 OCR 平等使用同一个能力池，同时保持在线请求无队列、离线节点可等待的既有服务语义。
- 保留算子内部为独立部署、显存安全和模型串行要求设置的队列、锁、线程池与批量限制。
- 提供覆盖配置、Redis 原子性、服务契约、跨服务链路、八算子启动和真实推理的分层验收清单。

**非目标：**

- 不把 `reported_inflight` 删除，也不要求心跳与租约在任意瞬间严格相等。
- 不给在线和离线流量划分保留配额、优先级或独立实例池，不承诺实例间轮询均衡。
- 不在 Online Gateway 或 Control Service 增加等待队列、重试队列或 Kafka 路径。
- 不改变现有算子推理路径、请求响应字段、模型逻辑、默认端口和 PPT 结果目录。
- 不把高频活跃租约逐条持久化到 PostgreSQL。
- 不把实例 ID、服务 URL、注册 Token、物理 GPU ID、NVIDIA 可见设备、配置路径、端口、worker、镜像、挂载、网络和 CPU/内存限制移入 TOML；这些仍是实例级、秘密或容器启动前的部署事实。

## 设计决策

### 1. 明确四种容量事实

系统统一使用以下定义：

| 名称 | 权威来源 | 用途 |
| --- | --- | --- |
| `max_concurrent_requests` | 算子根配置或受版本控制的本地安全模板 | 算子希望向平台注册的每实例总容量 |
| `declared_capacity` | 算子注册请求和 Redis 实例记录 | `max_concurrent_requests` 在平台注册协议中的字段名 |
| 活跃租约数 | Control Service 管理的 Redis 租约集合 | 平台是否可以继续分发的唯一占用值 |
| `reported_inflight` | 算子心跳 | 观测算子实际已接收请求数、发现绕过平台的调用或租约泄漏，不参与分配判定 |

正容量实例的可分发剩余量为 `max(declared_capacity - active_lease_count, 0)`。同一实例的租约集合按 `instance_id` 建立，能力只用于候选过滤，所以同一实例声明的多个能力天然共享一个池，不能把各能力容量横向相加。

不再采用 `max(active_leases, reported_inflight)`。该旧算法虽然能在调用方漏租约时保守降载，但会让已释放的短请求被滞后心跳继续占位。新设计用差异指标、日志和运维查询暴露异常，而不让观测值反向成为调度权威。

### 2. 八个算子使用统一平台配置段，但实例事实留在 Compose

每个项目在根配置或受版本控制的本地安全模板中增加本地安全默认值；已有 `[runtime]` 的项目在同一段内追加字段，不得创建重复 TOML 表。当前 FaceRec、OCR、Text Analysis 分别以 `config.example.toml`、`config.toml.example`、`config.example.toml` 作为 clean clone 可用且不含真实凭据的本地安全模板，其余五个项目使用根 `config.toml`：

```toml
[platform]
# 本地默认不主动向调度平台注册
registration_enabled = false
# 仅在 registration_enabled=true 时要求非空
control_service_url = ""
# 注册心跳间隔秒数，必须为有限正数
heartbeat_interval_seconds = 5
# 平台允许同时分发到本实例的工作单元数，必须为正整数
max_concurrent_requests = 10

[runtime]
# 本地默认不强制要求 CUDA；GPU 部署配置显式设为 true
require_gpu = false
```

`registration_enabled` 和 `require_gpu` 只接受严格布尔值；启用注册时 `control_service_url` 必须是非空 HTTP(S) URL；`heartbeat_interval_seconds` 必须是有限正数；`max_concurrent_requests` 必须是正整数，并拒绝 `0`、负数、布尔值、浮点数和字符串。非法配置必须在应用开始接收请求和注册前失败。八个项目的代码默认值必须与提交的根配置或本地安全模板一致：

| 算子 | 默认值 | 平台计量单位 | 继续保留的本地约束 |
| --- | ---: | --- | --- |
| ASR Online | 10 | 一个 WebSocket 会话 | 模型自身流式处理约束 |
| ASR Offline | 4 | 一次音频转写请求 | `concurrency=5` 的独立部署排队与现有模型串行锁 |
| FaceRec | 128 | 一次 `/recognize` 请求 | `thread.max_workers` 只控制 Dlib 本地进程池 |
| OCR | 256 | 一张图片请求 | `ocr.max_concurrency=1` 和引擎锁用于避免显存溢出 |
| ScreenDet | 128 | 一次 `/detect_all` 请求 | `max_batch_size` 只限制单请求图片数量 |
| PPT Slice | 10 | 一个后台切片任务 | 统一字段同时驱动本地任务管理器上限，移除同义旧字段 |
| VBas | 128 | 一次学生或教师图片批次请求 | 本地批次队列、模型锁和批次大小继续负责模型安全 |
| Text Analysis | 256 | 一次 `/v1/course_overviews` 或 `/v1/extract_keywords` 请求 | 接口内部的大模型并发、分片和重试不额外计入平台容量 |

VBas 当前学生、教师能力以及教师请求中的头部姿态处理共享 `128`；若 `teacher_head_pose` 作为能力标识参与路由，也只能过滤同一实例池，不能获得独立的 `128`。Text Analysis 只有 `course_overviews` 和 `extract_keywords` 进入平台注册，历史接口保持可用但不因此获得新的调度能力。

里程碑 2B 的八份 `deploy/config/operators/*.toml` 必须设置 `platform.registration_enabled=true`、`platform.control_service_url="http://control-service:18100"`、`platform.heartbeat_interval_seconds=5` 和对应确认容量。ASR Online、ASR Offline、FaceRec、OCR、ScreenDet、VBas 的部署 TOML 设置 `runtime.require_gpu=true`；PPT Slice 和 Text Analysis 设置为 `false`。

`operator_registry_client.install_operator_runtime` 改为由八个算子显式传入已经解析并校验的注册开关、Control Service 地址、心跳和容量，不再读取 `PLATFORM_REGISTRATION_ENABLED`、`PLATFORM_CONTROL_SERVICE_URL`、`PLATFORM_HEARTBEAT_INTERVAL_SECONDS` 或 `PLATFORM_DECLARED_CAPACITY`。六类 GPU 算子自身的设备检查改为读取 TOML 的 `runtime.require_gpu`，不再读取 `REQUIRE_GPU`。

以下字段仍由 Compose 或镜像负责，不能移入共享的算子类型 TOML：

| 字段/能力 | 保留位置 | 原因 |
| --- | --- | --- |
| `PLATFORM_OPERATOR_REGISTRY_TOKEN` | Compose 运行环境 | 管理凭据，不写入普通 TOML |
| `PLATFORM_INSTANCE_ID`、`PLATFORM_SERVICE_URL` | 每个 Compose service | 三个副本必须有不同身份和容器 DNS URL |
| `PLATFORM_GPU_ID`、`NVIDIA_VISIBLE_DEVICES`、GPU reservation | 每个 GPU Compose service | 表示宿主机物理卡和容器运行时设备绑定 |
| `CONFIG_PATH`、`OPERATOR_PORT`/`PORT`、`UVICORN_WORKERS` | Compose/镜像启动入口 | 应用读取 TOML 前及 Uvicorn 启动前已经需要 |
| 镜像、端口映射、挂载、网络、CPU/内存 | Compose | Docker 编排事实，应用内配置无法生效 |
| FaceRec MongoDB 凭据 | Compose 运行环境 | 独立基础设施凭据 |

`PLATFORM_MODEL_VERSION`、`PLATFORM_API_VERSION` 和可选实例标签继续作为发布/实例元数据环境变量兼容保留。`GPU_PROCESS_NAME` 从里程碑 2B Compose 删除，六类 GPU 镜像继续使用各自入口脚本已经存在的稳定默认进程名，并通过真实 GPU 进程证据验证。Compose 使用 YAML mapping anchors 收敛公共 Token、worker 以及每类算子的 `CONFIG_PATH`、容器端口等重复项；`docker compose config` 的展开结果仍是部署合同和 Harness 的校验输入。

PPT Slice 的 `task.max_concurrent_tasks` 与平台声明容量含义相同，迁移后由 `platform.max_concurrent_requests` 同时构造任务管理器和注册运行时。其他旧字段都与“平台可分发工作单元数”含义不同，必须保留，不能机械改名或覆盖。

### 3. 容量配置采用正整数并在启动期失败关闭

`max_concurrent_requests` 与注册协议中的 `declared_capacity` 均只接受正整数。缺失配置时使用各算子已经确认的代码默认值；显式配置非法值时，算子必须在开始接收业务请求和注册前失败，不能以无限容量、零容量或平台回退值继续运行。注册开关、地址、心跳、容量和 GPU 强制检查同样不得再被旧环境变量静默覆盖，使 TOML、注册状态和 Redis 分配计算始终只有一种配置语义。

### 4. Redis 活跃租约保存可归属的工作上下文

租约对象扩展为：

```json
{
  "lease_id": "...",
  "instance_id": "ocr-gpu0",
  "capability": "ocr",
  "service_url": "http://ocr-gpu0:8866",
  "acquired_at": "2026-08-19T10:00:00Z",
  "expires_at": "2026-08-19T10:01:00Z",
  "work_context": {
    "source_service": "orchestrator-service",
    "work_type": "ppt_ocr_item",
    "work_id": "ppt-image-008",
    "task_id": "course-task-001",
    "node_id": "1234",
    "item_id": "ppt-image-008",
    "trace_id": "..."
  }
}
```

`source_service`、`work_type` 和 `work_id` 在出现 `work_context` 时必填；`task_id`、`node_id`、`item_id` 和 `trace_id` 可空，因为在线请求不一定属于课程任务。上下文只能包含短标识符，不得保存 Base64、媒体、OCR 文本、ASR 文本或请求正文。

租约创建、过期清理、容量比较和写入继续在单个 Lua 脚本中使用 Redis `TIME` 原子执行。`acquired_at` 创建后不变；续租只更新 `expires_at` 和 Redis TTL；释放、过期、实例注销或 Redis 运行标识变化时删除对应明细。

租约申请请求增加可选 `work_context`，使已知身份的在线请求和图片工作项能够一次写入。另新增：

```http
POST /internal/operator-instances/lease/context
GET  /ops/operator-instances/{instance_id}/active-leases
```

上下文绑定接口接收 `lease_id` 和完整 `work_context`。它必须原子确认租约仍有效：相同上下文的重复绑定幂等成功，已绑定不同上下文时返回冲突，过期或不存在时返回未找到。查询接口先清理过期/失效记录，再返回实例的活跃租约；未绑定项显式标记为未绑定，不能猜测 `task_id`。

这些接口沿用 Control Service 现有内部网络和运维鉴权边界，不新建另一套身份系统。活跃明细只存 Redis，PostgreSQL 继续保存任务事实和低频注册审计。

### 5. 普通调用与扇出节点使用不同租约作用域

“一个真实工作单元一个租约”按以下粒度执行：

| 调用链 | 一个租约对应 |
| --- | --- |
| 在线 ASR | 一个 WebSocket 会话，从连接建立持有到关闭，长会话持续续租 |
| 离线 ASR | 一次音频转写请求 |
| FaceRec | 一次 `/recognize` 请求 |
| ScreenDet | 一次 `/detect_all` 请求 |
| 在线 OCR | 一张图片的一次 `/ocr/prediction` 调用 |
| PPT Slice | 一个后台切片任务，从受理到终态持久化，期间持续续租 |
| PPT OCR | 一张 PPT 图片的一次 `/ocr/prediction` 调用 |
| PPT 关键词 | 一张 PPT 图片的一次 `/v1/extract_keywords` 调用 |
| 课程脑图 | 一次 `/v1/course_overviews` 调用 |
| VBas | 一次学生或教师图片批次 HTTP 调用；可选头部姿态仍属于同一次教师调用 |

普通节点继续采用“先取得租约，再领取节点”的顺序，避免领取后才发现没有容量。领取成功后立即调用 `/internal/operator-instances/lease/context` 绑定 `task_id`、`node_id` 和追踪信息；没有可领取节点则马上释放未绑定租约。

`PPT_OCR` 和 `PPT_KEYWORDS` 是工作项型节点。协调节点本身不是算子请求，不得持有覆盖整个节点生命周期的额外租约。调度器需区分节点级与工作项级租约作用域：领取工作项型节点后，由 `PptTextPipeline` 在每个受本地 `PptWorkLimits` 控制的协程内申请对应能力租约，携带图片 `item_id`，调用该租约选中的实例，并在单项结果持久化后释放。不能把一个实例 URL 固定给整批图片，也不能在 OCR 节点外层再占一个 OCR 租约。

工作项暂时取不到租约时属于容量等待，不应把课程任务标记失败。已运行的其他工作项继续完成，待容量出现后继续领取。平台租约限制跨服务总量，本地 `PptWorkLimits` 只限制单个编排进程的协程扇出，两者同时生效但不重复计量。

Vision Orchestrator 已按 VBas HTTP 批次取租约，需要补充 `task_id`、批次 `work_id` 和流类型上下文；一个批次中的多张帧不拆成多个租约。Text Analysis 的大模型内部扇出完全位于一次 HTTP 请求之内，不向 Control Service 派生子租约。

HTTP 请求超时与租约生命周期相互独立。每个同步 HTTP 算子调用必须配置有限硬超时；租约使用独立且更短的 TTL，调用尚未完成且租约接近过期时由调用方周期续租。调用完成或失败后立即释放租约；调用方进程崩溃或失联后续租停止，由 TTL 自动清理。续租失败时，调用方不得继续派生新的算子工作，并按当前在线错误或离线恢复语义结束本次调用。

新租约申请仍要求实例具有有效心跳、`ONLINE` 生命周期和模型就绪；但是已经取得且尚未过期的租约续期不得仅因算子心跳 TTL 短暂过期而失败。模型推理可能在单 Worker 进程中短暂阻塞心跳协程，而调用方的续租本身仍证明该工作存活。心跳缺失期间该实例继续拒绝新租约，既有租约继续占用容量；实例注销、同 ID 重注册、显式 `OFFLINE`、Redis 运行标识变化或租约自身到期仍使续租失败。

### 6. 在线和离线共享容量，但等待策略不同

Control Service 的分配器只按能力、实例生命周期、模型就绪、心跳有效和共享池剩余容量选择实例，不根据 `source_service` 为在线或离线保留槽位。现有按实例标识的确定性选择可以保留；“平等共享”表示两类来源使用同一准入规则，不代表轮询均衡，也不要求消除对 `ocr-gpu0` 的正常偏向。

容量不足后的行为保持调用场景边界：

- 在线 HTTP 请求不进入平台队列。Control Service 内部租约接口返回 HTTP `503`，Online Gateway 将其映射为 HTTP `200`、业务码 `50301` 返回上游 A 服务。
- 在线 ASR 在握手后发现无容量时发送业务错误并以当前约定关闭；不创建离线节点。
- 离线节点或图片工作项保持/回到容量等待状态，保留已有结果并由调度循环后续重试；Control Service 的 `503` 不是给 A 服务的课程终态响应。
- 算子内部可以继续排队或串行处理已经被平台分发的请求。活跃租约表示“已分配到实例，正在执行或在实例内部等待”，不等同于正在占用 GPU 核心。

Vision Orchestrator 的 Kafka Consumer 必须将租约申请返回的“暂无可用算子容量” HTTP `503` 单独识别为可恢复容量等待。当平台容器先于 VBas 实例启动，或已注册 VBas 暂时满载时，Consumer 保留当前消息、不提交 offset，按 `worker.poll_interval_seconds` 原地重试，且后台循环仍属于存活状态；`/ready` 不得仅因正在等待算子容量而失败。服务关闭时必须立即终止等待且仍不提交该消息。算子注册中心不可用等其他 HTTP `503`、HTTP `400/401`、非法租约响应或其他协议/配置错误仍是后台循环故障，应使 `/ready` 失败，不能被容量等待逻辑掩盖。

VBas 实例在已取得平台租约后仍可能因本地批次队列或模型安全保护返回 HTTP `429`。该结果同样属于可恢复过载：调用方必须释放当前尝试的租约，仅对该批次按可中断间隔重试，保留同一命令中已成功的兄弟批次，直到所有批次成功后才允许提交 Kafka offset。这避免平台容量大于 VBas 本地并发时，每次整命令重试都形成“一批成功、一批 `429`”的活锁。只有非容量的致命异常才取消并收割所有未完成兄弟批次，确保不留下孤立请求。服务关闭信号和调用方取消都必须立即打断单批次等待；VBas 普通 HTTP `503` 仍按算子调用故障处理，不得无条件并入容量等待。

### 7. Online Gateway 提供单图 OCR 适配而不改变 OCR 算子

新增：

```http
POST /api/online/ocr/recognize
```

网关请求采用单图语义：

```json
{
  "image_id": "frame-001",
  "image": "data:image/png;base64,...",
  "enable_formula": false
}
```

`image` 必填并按 Online Gateway 的统一正文、Base64 大小和格式规则校验；`image_id` 可省略，省略时网关生成本次请求内唯一标识；`enable_formula` 可省略且严格默认为 `false`。网关把请求转换为 OCR 现有的单元素 `key`、`value` 数组和 `enable_formula`，调用 `/ocr/prediction`，不修改 OCR 算子协议。

Online Gateway 根 `config.toml` 使用以下边界，并在解析完整业务模型和申请租约前实际执行，而不是只保留配置字段：

```toml
[body]
# JSON 请求体最大 72 MiB，为单图 Base64 编码和 JSON 包装保留余量
max_bytes = 75497472

[base64]
# 单张图片完成 Base64 解码后的文件内容最大 50 MiB
max_decoded_bytes = 52428800
```

该正文和 Base64 限制适用于 Online Gateway 的在线图片入口。OCR 自身根配置和受控部署配置同步设置 `ocr.image_max_bytes=52428800`，使在线 OCR 与 PPT OCR 都受 50 MiB 单图边界保护。若部署反向代理，其请求体上限必须不小于 72 MiB。

成功响应继续使用 Online Gateway 的 `BusinessResponse`，`data` 原样承载 OCR 算子的响应对象，使 `key`、`value`、`formula_results`、`err_no` 和 `err_msg` 语义不被第二次改造。网关在调用前申请 `ocr` 租约并附带在线工作上下文，在收到完整响应或异常后释放。

参数错误返回业务码 `40001`，容量不足返回 `50301`，算子 HTTP/响应格式失败返回 `50000`；均保持在线网关现有 HTTP `200` 业务响应风格。该接口不创建课程任务、不发布 Kafka、不访问 PPT 共享目录，也不改变既有 PPT OCR/关键词链路。

### 8. 可观测性采用“可归属事实 + 差异”，不伪造执行状态

实例活跃租约查询回答“当前哪些工作已分配给该实例”，而不是“GPU 当前正计算哪一个工作”。查询结果同时给出 `active_lease_count`、`reported_inflight` 和差异值，便于发现以下情况：

- `reported_inflight > active_lease_count`：可能存在直连算子请求、心跳时差或调用方漏取租约。
- `active_lease_count > reported_inflight`：请求可能仍在网络途中、实例内部排队、心跳尚未更新或租约未及时释放。
- 未绑定租约：调度器已取租约但尚未领取/绑定节点，或调用方未提供上下文。

监控可以告警，但不得把差异自动转换为虚构任务，也不得再次用于拒绝分发。指标和日志不得包含请求正文、图片或识别文本。

### 9. 里程碑 2B 精确替换并清理旧平台/算子镜像

本变更 apply 后，`192.168.29.11` 必须使用最终 Git SHA 重新构建四个平台服务和八类算子镜像。构建前记录当前平台/算子镜像引用、镜像 ID、revision 标签、大小及引用它们的容器；新镜像必须先完成构建和 `org.opencontainers.image.revision` 校验，再替换对应容器并通过基础健康、24 实例注册及算子 Smoke。

维护事务和算子容器账本具有不同生命周期。若一个 release 已建立合法 direct maintenance，但在发布 `baseline/new` 前中断，后续 release 的只读账本 resolver 可在严格验证 maintenance 后，沿该 release 当前 UID 所有、单链接、`0400` 的 predecessor marker 查找同 tag 的更早完整账本对。该路径不得创建或改写历史 marker/provenance，也不得只凭 marker 继承；仍须验证账本排序、完整容器 ID、Docker/Compose 身份，并证明当前容器集合减去 resolved baseline 与 resolved new 字节级一致。direct 状态缺 marker、marker 非法、partial、环或容器集合不一致时必须失败关闭。

这里的合法 direct maintenance 同时包括仍有活动 paused ledger 的状态，以及已经通过唯一 `0400` 终态 audit 完成 restore 的 completed direct 状态。后者仍必须重新校验 snapshot、终态 audit、当前容器恢复事实和 predecessor marker；completed 状态本身不能作为缺失算子账本的替代证据。

新版本通过上述门禁后，允许删除已经不被任何容器引用、且能够由本次工作区 Compose 镜像槽位和旧 release revision 同时证明身份的旧平台/算子镜像。删除必须使用清单中的精确镜像引用或镜像 ID，禁止使用 `docker image rm -f`、未解析变量、宽泛名称匹配、`docker system prune` 或删除 Docker 数据目录。基础 CUDA/Python 镜像、PostgreSQL、Redis、Kafka、MongoDB 镜像、服务器原有业务镜像（包括原 `ocr-v6-amd`）、模型资产、数据卷、`/data/course`、`/data/result` 和历史 release/Harness 证据不属于清理范围。

平台容器替换前必须先对生产任务库幂等执行当前待发布的 `0006_course_task_type_submission.sql` 并核验 `submission_id` 的 UUID 类型、非空约束和中文说明；历史基础表或字段状态不符合已知前置版本时失败关闭，不允许依赖 Control Service readiness 超时来发现迁移遗漏。

部署 runtime preflight 的 PostgreSQL 权威列集合必须与 Control Service readiness 使用的 `CONTROL_SCHEMA_COLUMNS` 保持完全一致，并由跨边界自动测试锁定；前向迁移增加必需列时，两处对账合同必须在同一提交中同步更新。

Canonical 总控生成业务 Campaign 命令时必须逐项忠实传递既定选项和值，不得把格式字符、补丁标记或其他未声明 token 注入 argv。该合同必须由真实 shell 执行生成命令并捕获 argv 的自动测试覆盖，不能只检查命令字符串包含阶段名称。

业务 Campaign 的 8 项 B 级质量复核不能在当前 release 的真实课程结果产生前预制，也不能复用旧 SHA 的结论。`offline` 四任务完成后，Campaign 必须先发布包含当前完整 Git SHA、课程 `task_id` 和 7 个离线复核 case 的 write-once 请求，再有界等待独立复核索引；`vision` 结果完成后以同一课程身份追加等待 `VIS-025`。复核发布器先把每个脱敏 JSON 证据原子写入当前 release 的 `business/reviews/`，再原子更新 Git 外受限目录中的索引。索引和每项证据都必须属于当前 UID、权限 `0600`、单硬链接、路径祖先无符号链接，并对账 `case_id/git_sha/task_id/status/reviewer/observed`；课程图片、联系表、ASR/OCR 全文只允许保存在 Git 外受限目录，普通 release 证据只记录摘要、散列和不透明证据编号。索引缺项时可在限定时间内等待，出现旧 SHA、旧课程、非法路径或元数据时立即失败关闭。

deployment 变异用例会真实重启 Kafka 及若干平台服务。`LOAD-014` 不得只校验 Orchestrator，必须同时等待 Orchestrator 与 Vision Orchestrator 的 Kafka Consumer readiness。deployment 阶段全部结束后、offline Campaign 启动前，Canonical 还必须再次探测这两个离线后台服务；只允许精确重启当前不健康的服务，随后重跑 runtime preflight，不得扩大为 Control、Online Gateway 或基础设施整体重启。

若新镜像构建、revision 校验、容器健康、注册或 Smoke 任一步失败，旧镜像不得删除。若旧镜像仍被运行中、暂停或停止容器引用，清理步骤必须报告并跳过，不能强制删除。清理后不再具备旧镜像的本机即时回滚能力；旧 Git SHA、配置和 Harness 证据继续保留，确需回滚时从旧 SHA 重新构建或从可信镜像源重新取得。

若新镜像已构建或替换但后续门禁失败，Canonical 的 `EXIT` 恢复路径必须保留原退出码，先完整验证 baseline/new 账本和每个容器身份，再停止本轮精确 new ledger 并恢复已授权的原业务。账本或容器身份不可证明时必须失败关闭，不得执行宽泛停止或恢复。

Python 总控必须把外层 Bash 放入独立 session，并在收到 `SIGHUP/SIGINT/SIGTERM` 时先枚举其子进程树：`operator_lifecycle.py hold-lock` 及其后代必须保留，其他运行工作进程收到 `SIGTERM`，随后再终止外层 Bash 并等待 `EXIT` trap 完成。不得对整个进程组广播信号，否则会在恢复前释放 release-tag 锁；也不得在 Bash 仍等待长子进程时只发送信号后立即返回。

算子身份校验依赖从权威 Compose 生成的 24 项 service allowlist。该 allowlist 和其他验证临时文件必须保留到失败恢复完成；只能在精确 new ledger 核验、停止和原业务 restore 之后清理，不能在 `EXIT` trap 入口提前删除。

### 10. Vision Orchestrator 对本地抽帧进程执行独立限流

视觉粗粒度扫描可能一次生成数百个时间点。`FFmpegFrameExtractor` 不得把全部时间点无界提交给 `asyncio.to_thread`，否则单个课程就会同时启动大量 ffmpeg 进程，并在服务容器的内存 cgroup 内触发 OOM。根 `config.toml` 的 `[media]` 增加正整数 `max_concurrent_processes`，默认值为 `2`；同一个服务进程中的时长探测、教师抽帧和学生抽帧共享一个信号量。

该配置只限制本地 ffmpeg/ffprobe 子进程数，不减少扫描时间点，不改变 `scan.batch_size`、`vbas.max_concurrency`、VBas 租约粒度或任何算子的 `declared_capacity`。里程碑 2B 的 Vision Orchestrator 容器继续使用 `4G` 默认内存限制，并必须在真实 T/S 长视频粗扫期间证明没有 cgroup OOM。Compose 健康检查使用 `/ready` 而不是仅表示进程存活的 `/health`，使 Kafka consumer 后台循环退出或依赖失效能够进入部署健康事实。

候选窗口数与抽帧子进程并发是两个独立的保护维度。真实长课程可以合理产生超过 `20` 个不连续行为候选窗口；这不等于无界执行。`scan.max_candidate_windows` 因此保留为正整数上限，默认调整为 `128`，并继续与 `scan.max_detection_points=10000` 共同失败关闭。验收必须覆盖 `31` 个窗口正常进入加密检测，以及第 `129` 个窗口被确定拒绝。

## 风险 / 取舍

- **高默认容量会让 OCR、ASR Offline 等串行算子形成更长的实例内等待** → 默认值是用户确认的平台准入上限，不是并行推理承诺；保留本地锁并增加租约/心跳差异、延迟和错误观测，后续可通过各算子 TOML 调整声明容量。
- **只按活跃租约调度会放大未通过平台直连算子的过载风险** → 直连属于独立部署兼容场景，使用 `reported_inflight` 差异告警暴露；平台调用必须通过统一租约客户端，跨服务测试验证无漏租约路径。
- **租约在请求完成前过期会造成重复分发** → HTTP、长任务和 WebSocket 的有限请求/会话超时与短租约 TTL 分离；所有可能跨越 TTL 的调用周期续租，续租失败时停止继续派生工作并进入现有失败/恢复流程。
- **模型推理阻塞算子心跳会误杀在途租约** → 新租约继续要求有效心跳，但有效在途租约的续期不以心跳 key 存活为条件；心跳恢复前实例不会获得新工作，注销、重注册、显式离线和租约 TTL 仍能终止旧租约。
- **工作上下文绑定发生在取租约之后，会短暂出现未绑定项** → 这是先取容量再领取节点的必要窗口；相同上下文幂等绑定、冲突拒绝，并通过 `context_status` 明确展示而不猜测。
- **工作项型节点改造不当可能重复占租约或固定到一个实例** → 明确节点级/工作项级作用域，在单元和 Redis 集成测试中断言 N 张图片最多产生 N 个同时有效的项目租约且没有外层同能力租约。
- **确定性实例选择可能偏向按标识排序靠前的实例** → 当前用户接受该偏向，本变更只保证来源平等和原子容量，不引入新的负载均衡算法。
- **在线图片入口可能接收超限正文或 Base64** → 网关在取租约前强制执行 72 MiB 正文和 50 MiB Base64 解码限制，OCR 再执行 50 MiB 单图限制，且日志不记录图片内容。
- **跨八个仓库同时切换配置会产生版本漂移** → Control Service 和公共包先向后兼容上线，再分批更新算子；部署预检核对八个实例注册值和 Compose 中已无迁移后的平台/GPU环境变量覆盖。
- **把类型级配置移入 TOML 后，根配置和部署配置可能漂移** → 对八组根/部署 TOML 建立字段完整性、严格类型和确认值对比测试；部署预检从实际挂载 TOML 读取注册与 GPU 要求，不再从已删除环境变量猜测。
- **YAML anchors 可能让源文件易读但展开结果发生意外覆盖** → 所有部署合同和 Harness 同时校验源文件与 `docker compose config` 展开后的 24 实例，确认实例 ID、服务 URL、Token、端口和 GPU 绑定没有丢失。
- **删除旧平台/算子镜像会失去本机即时回滚能力** → 只在新版本完成 revision、健康、注册和 Smoke 门禁后按精确 ID 删除，保留旧 Git SHA、配置和不可变 Harness 证据；失败时不执行清理。
- **长视频粗扫的抽帧时间点可能形成进程峰值** → 所有 ffmpeg/ffprobe 调用共享 `media.max_concurrent_processes` 信号量；通过并发单元测试和 4 GiB 容器内真实 T/S 视频运行共同验证，禁止仅提高容器内存掩盖无界并发。
- **长课程可以产生超过旧默认值 `20` 的合理候选窗口** → 不删除上限，而是将可配置默认值收敛为 `128`，并保留 `max_detection_points` 第二道保护；真实长课程和 31/129 窗口边界测试共同验证。

## 迁移计划

1. 先扩展公共契约和 Control Service，统一正整数容量校验，并增加上下文、活跃租约查询及新的调度占用算法。
2. 更新各租约客户端，允许申请时携带上下文、申请后绑定上下文，并为可能超过单次 TTL 的同步 HTTP、后台任务和 WebSocket 调用提供续租；旧调用不传上下文时仍可工作。
3. 更新 Orchestrator 与 Vision Orchestrator 的节点级/工作项级租约粒度，先通过 Stub 和真实 Redis 验证无重复占用，再接真实算子。
4. 更新 Online Gateway 单图 OCR 路由和 72 MiB/50 MiB 可执行限制，完成与 OCR 契约替身和真实 OCR 的验证。
5. 分别更新八个算子的配置模型、根配置或受版本控制的本地安全模板、显式注册参数、GPU 检查、测试和 README；PPT Slice 同步迁移本地任务上限。
6. 更新八份部署 TOML 和算子 Compose，删除已迁移的平台/GPU环境变量、增加 YAML anchors，并从实际挂载 TOML 与展开后的 Compose 双向预检注册值、实例身份和 GPU 标签。
7. 执行分层验证和 24 实例 Harness 场景，确认在线/离线 OCR 同池竞争、容量释放及时、活跃任务可查询、既有路由和真实推理不回归。
8. 在 `192.168.29.11` 记录新旧镜像清单；新镜像完成构建、revision、容器替换、健康、注册和 Smoke 后，按精确 ID 删除不再被引用的旧平台/算子镜像并记录释放空间。
9. 以真实长课程复核候选窗口上限；允许已观测的 `31` 个窗口进入加密检测，超过配置的窗口仍失败关闭。

Redis 租约都是有 TTL 的临时状态，不需要数据迁移。删除旧镜像前的回滚演练应先停止新分发并等待或释放新格式租约，再回滚编排和算子；旧版 Control Service 不应在仍有新格式长租约时直接接管。配置回滚需要恢复与旧二进制匹配的 Compose 环境变量和 PPT 旧字段，不能只回滚单个文件。旧镜像清理完成后，回滚还需要从旧 Git SHA 重新构建或从可信镜像源重新取得对应镜像。

## 待后续决策

- 是否在后续版本增加按来源、课程优先级或能力维度的配额；本变更明确不预留容量。
- 是否在后续版本改为最少租约数或轮询实例选择；本变更保留现有确定性选择。

## 背景

`control-service` 当前通过 `RedisOperatorRegistry._LEASE_SCRIPT` 读取 capability 成员、按 `instance_id` 排序，并返回第一个 `active_leases < declared_capacity` 的实例。该算法可以防止租约超过声明容量，但不会比较实例当前负载；在三个 VBas 实例都声明大容量时，排序第一的 `vbas-gpu0` 会长期被选择。

真实任务 `test-260827` 进一步证明了该问题：约 2880 秒的 S 视频按 10 秒抽帧，分别执行全画面、前排和后排三轮分析，每 8 帧一个批次，共形成 108 个成功 VBas 批次；`vbas-gpu0` 接收全部成功批次并出现额外满载拒绝，`vbas-gpu1`、`vbas-gpu2` 对该精确任务均未接收批次。

当前还存在两个相关实现缺口：VBas 的注册心跳没有显式使用 `BatchAdmissionController.running_batches`；Vision Orchestrator 的 `[worker].concurrency` 未形成课程 Worker，`[vbas].max_concurrency` 的信号量又在每次课程分析中独立创建。若只替换实例评分，单课程和多课程仍无法形成可控、可证明的三卡并发。

本变更不修改 A 服务或算子业务协议。目标环境继续是 `192.168.29.11` 的七算子、四平台、三 GPU 单机拓扑，媒体继续从 `192.168.29.12:5555` 下载，共享 `/data/course` 和 `/data/result`。

## 目标与非目标

**目标：**

- 让每次算子租约基于实例当前有效负载选择，而不是先填满排序第一实例。
- 保证并发选择与租约创建在 Redis 中原子完成，相同健康和容量实例不会持续饥饿。
- 让 VBas 上报真实运行批次数，并固定部署配置为 `1024/1024/0`。
- 将 Vision 的 `max_concurrency=16` 解释为服务级共享的 VBas 批次并发，保持 `max_batch_size=8`。
- 让课程 Worker 并发配置真正生效，并保持 Kafka 至少一次交付与节点幂等边界。
- 用 20 个不同 `task_id` 的真实 `STUDENT_BEHAVIOR` 北向请求证明三个 VBas 实例均获得批次。
- 用 Online Gateway 同时发起 1000 个合法单图 VBas 请求，证明在线请求也由公共路由分散到三个实例并完整释放租约。
- 让 20 个离线学生行为任务与 1000 路在线 VBas 请求真实重叠，证明两类调用方共享容量且不会因突发流量导致固定实例独占或任务丢失。
- 在新版本验证通过后精确删除被替代的旧容器和旧镜像，同时保留构建缓存。

**非目标：**

- 不在本变更判断 `1024` 是否符合 VBas 模型、显存或延迟的真实稳定容量。
- 不启用或实现 VBas 本地等待队列，`MaxQueueSize` 固定为 `0`。
- 不改变抽帧间隔、自适应视觉策略、学生聚合算法、证据图片或数据库结果结构。
- 不改变 `POST /api/course-jobs`、任务查询、在线接口、请求字段、响应字段和整数状态。
- 不引入 Kubernetes、服务网格、NFS 或新的外部中间件。

## 技术决策

### 1. 使用原子加权最少负载选择

租约脚本先清理候选实例的过期/失效租约，再仅保留 capability 匹配、心跳有效、`ONLINE`、`model_ready=true` 且未达到声明容量的实例。每个候选实例计算：

```text
active_lease_count = Redis 中该实例当前有效租约数
reported_inflight = 最近一次算子心跳上报的实际处理中数量
effective_inflight = max(active_lease_count, reported_inflight)
load_ratio = effective_inflight / declared_capacity
```

`active_lease_count` 与租约创建处于同一 Lua 原子操作中，负责消除并发选择竞态；`reported_inflight` 可能受心跳周期影响，只用于避免直连请求、租约丢失或短时差异造成负载低估。二者不能相加，否则平台租约请求会被重复计数。

Lua 中使用交叉乘法比较两个比例，避免浮点精度影响：

```text
left.effective_inflight * right.declared_capacity
    < right.effective_inflight * left.declared_capacity
```

当最低负载候选不止一个时，按 capability 维度维护 Redis 轮询游标；先稳定排序最低负载候选，再由原子递增游标选择。选定后在同一脚本内创建租约并立即使该实例后续评分增加。

备选方案“随机选择”不能提供可重放证据；“只比较 reported_inflight”存在 5 秒心跳竞态；“继续 first-fit 并降低容量”只能偶然迫使流量溢出到后续实例，均不采用。

### 2. 声明容量仍是共享硬上限

一个 VBas 实例的 `student_behavior` 和 `teacher_behavior` 继续共享同一个实例租约集合和 `declared_capacity`。在线与离线调用也共享该集合。实例负载评分统计该实例全部 capability 的有效租约，不能给同一 GPU 按能力重复计算容量。

本次固定：

```toml
[platform]
max_concurrent_requests = 1024

[TIAS]
MaxConcurrentBatches = 1024
MaxQueueSize = 0
```

平台注册字段仍为 `declared_capacity=1024`。一个包含 8 张图片的 VBas HTTP 批次只占一个租约槽，不按 8 个请求计数。

### 3. VBas 心跳绑定真实批次控制器

VBas 安装统一算子运行时时传入 `inflight_provider`，从 `worker_controller.snapshot()["running_batches"]` 返回有限非负整数。心跳、`/ops/status` 和 VBas Worker 状态在稳定采样点必须一致；短暂差异通过 Control 的 `attribution_difference` 暴露，不覆盖 Redis 活跃租约事实。

`MaxQueueSize=0` 时，超过本地批次上限继续返回明确的过载响应。VBas 不保存等待批次，避免任务已经绑定某个实例后在其内部排队，而其他实例仍然空闲。

### 4. Vision 的 16 个槽位是服务级全局并发

保持：

```toml
[vbas]
max_batch_size = 8
max_concurrency = 16
```

`VbasBatchClient` 在服务启动时创建一个共享信号量或固定数量的批次 Worker，所有课程和全画面/前排/后排轮次共同使用这 16 个槽位。不得在每次 `analyze()` 中新建 16 个独立槽位。批次队列必须有界；发生首个不可恢复的同课程批次错误时，只取消该课程尚未开始的批次，不取消其他课程。

每个批次在取得全局槽位后申请 Control 租约，通过租约返回的 `service_url` 直接调用 VBas，并在响应、错误或取消路径精确释放租约。Control 返回全部实例无容量时，Vision 有界等待后重试；VBas 返回过载时释放当前租约并重新进入选择，不能无限死磕同一个实例。

### 5. `[worker].concurrency` 形成有界课程 Worker

Vision Consumer 按 Kafka partition 维护在途消息和连续完成 offset。最多同时执行 `worker.concurrency` 个课程命令；完成顺序不一致时只能提交每个 partition 已连续完成的最高 offset，不能越过尚未完成消息。达到并发上限时暂停领取或保持有界缓存，不能无界创建协程。

停止时先停止领取新消息，再在 `shutdown_timeout_seconds` 内等待在途课程；未完成消息不提交 offset，重启后依赖节点状态和处理器幂等逻辑安全重放。确定性单任务失败仍写入节点失败终态并提交对应消息，PostgreSQL/Kafka/进度发布等基础设施失败继续失败关闭。

本变更让已有 `worker.concurrency` 生效，但不把 A 服务一次提交 20/128 个任务解释为必须同时运行 20/128 个课程。Control 可异步接受并由 Kafka 排队，实际课程并发继续由该配置控制。远端 20 请求验证可以通过受控部署配置提高课程并发，但不得把 20 固化成不可调整的接口合同。

### 6. 真实验证使用北向请求与三类证据

负载程序并发调用 `http://192.168.29.11:18100/api/course-jobs` 20 次，每次只替换唯一 `task_id`，其余字段固定为：

```json
{
  "task_id": "vbas-balance-<run>-001",
  "task_types": ["STUDENT_BEHAVIOR"],
  "priority": "NORMAL",
  "teacher_video_path": "",
  "student_video_path": "http://192.168.29.12:5555/course/%E4%B8%9C%E5%8D%97%E5%A4%A7%E5%AD%A6-%E6%9D%8E%E9%AA%8F%E6%89%AC/%E8%AE%A1%E7%AE%97%E6%80%9D%E7%BB%B4%E4%B8%8E%E7%A8%8B%E5%BA%8F%E5%AE%9E%E8%B7%B5II_202520263B61G060201_%E6%9D%8E%E9%AA%8F%E6%89%AC_2026%E5%B9%B45%E6%9C%8821%E5%8F%B714%E6%97%B60%E5%88%86/%E5%AD%A6%E7%94%9F1.mp4",
  "slides_video_path": "",
  "front_points": [
    {"X": 0, "Y": 0},
    {"X": 1920, "Y": 0},
    {"X": 1920, "Y": 540},
    {"X": 0, "Y": 540}
  ],
  "back_point": [
    {"X": 0, "Y": 540},
    {"X": 1920, "Y": 540},
    {"X": 1920, "Y": 1080},
    {"X": 0, "Y": 1080}
  ],
  "student_count": 70,
  "asr_options": null
}
```

权威证据包含：Control 的实例/活跃租约时序和批次归属、三个 VBas 容器按精确 `task_id/batch_id` 汇总的接受/过载/终态日志，以及 20 个课程查询结果。`nvidia-smi` 的进程、利用率和显存时序只是 GPU 活跃补充，不能单独证明某个批次路由到了哪个实例。

当三个实例均健康、容量相同且存在至少三个可并行批次时，首次三个原子租约必须落到三个不同实例；整个验证窗口三个实例都必须接收真实批次，且不能在其他实例持续空闲时由一个实例长期独占。若实例处理速度不同，完成批次数可以不同，报告不得用机械三等分否定“更快实例处理更多批次”的合理结果。

### 7. 千路在线 VBas 请求独立验证

在线 VBas 使用既有 `IMG-VBAS-1000` 用例，但与离线 20 课程任务分开执行和出具结论。负载程序同时向 `http://192.168.29.11:18103/api/online/vbas/analyze` 发起 1000 个请求；每个请求只包含一张不超过 5 MiB 的合法真实图片，使用唯一 `ImageId` 和链路标识，`stream_type` 固定为 `student`。不得把多图请求拆成多个实例调用，也不得直连 VBas 绕过 Online Gateway。

执行前确认三个 VBas 实例均健康、空闲、容量相同且注册 `student_behavior`，Online Gateway 的 `max_connections=2048`、`max_keepalive_connections=512` 和超时配置已实际生效。1000 个请求的目标通过条件为：全部完成既有业务成功响应，不产生 `50301`、`50000`、网关连接池错误或未解释超时；三个 VBas 实例都收到真实请求；首次三个原子租约覆盖三个实例；租约取得数、实例调用数和完成请求数可核对，停止负载后租约与实例在途数归零。

实例处理速度可以不同，因此不要求最终请求数机械三等分；但在三个实例健康且空闲时，不允许固定实例长期独占。权威证据为 Online Gateway 按实例请求指标、Control 租约申请/取得/释放时序、三个 VBas 容器按唯一 `ImageId` 汇总的日志和 1000 个响应分类。`nvidia-smi` 只作为 GPU 活跃补充。

负载机必须在执行前通过文件句柄、可用端口、连接池和网络基线预检。如果负载机未真正形成 1000 个同时在途请求，该次执行无资格判定平台通过；负载机自身资源耗尽必须与网关、Control 和 VBas 错误分开记录。

### 8. 二十离线任务与千路在线请求重叠验证

新增专用用例 `MIXED-VBAS-OFF20-ONLINE1000`。执行器先并发提交 20 个不同 `task_id` 的 `STUDENT_BEHAVIOR` 课程任务，但不能在提交响应后立即假定已经产生混合负载；它必须持续观测 Control 租约，直到 `source_service=vision-orchestrator-service` 的真实 VBas 活跃租约达到预设重叠门槛且三个实例均有离线批次归属，才同时释放 1000 个 Online Gateway 单图请求。若离线租约没有在限定时间内形成，该场景失败关闭，不能退化为单独在线测试。

混合窗口内，Vision Orchestrator 最多使用服务级共享的 16 个 VBas 批次槽位，Online Gateway 最多形成 1000 个请求；两者必须通过 Control 的同一实例租约集合和 `declared_capacity=1024` 评分，不能按调用方、capability 或在线/离线分别建立容量池。三个实例健康且从空闲开始时，声明总容量为 3072，测试目标要求 1000 个在线请求全部成功，20 个课程任务保持合法状态并最终完成，不得出现容量池重复计算、租约超卖、固定实例独占或在线突发导致离线任务失败。

本场景不要求在线和离线获得相同请求数，也不要求三实例机械三等分。它要求混合窗口内三个实例都有真实工作；在线请求不能只命中一个实例；离线批次在在线突发期间或突发结束后的有界时间内必须继续取得租约和推进，不能持续饥饿。每个实例的全部在线、离线、教师和学生能力租约总和不得超过该实例声明容量。

证据必须按 `source_service`、`work_type`、`task_id`、`batch_id`、在线链路标识和 `instance_id` 区分两类流量，记录峰值活跃租约、取得/拒绝/释放计数、Online Gateway 响应分类、20 个课程节点进度、三个 VBas 的请求日志及 GPU 时序。测试停止后，1000 个在线请求、20 个课程终态、活跃租约、VBas `running_batches`、Vision 在途批次和 Kafka 积压必须按各自合同收敛。

### 9. A 服务契约保持兼容

本变更只改变内部租约选择、并发和部署配置。A 服务继续使用相同的 `POST /api/course-jobs` 和查询接口，字段 `task_id`、`task_types`、`priority`、三个视频路径、`front_points`、`back_point`、`student_count`、`asr_options` 均保持原名、可选性和语义。同步响应仍表示异步任务受理/复用，最终结果仍由原查询接口取得。

## 风险与权衡

- **风险：`1024` 超过 VBas 真实稳定容量。** → 本变更按用户确认值配置但不宣称容量验证通过；远端执行保持 GPU、容器、错误和护栏观测，出现 OOM/Xid/重启时停止新负载并保留证据。
- **风险：心跳滞后导致评分不准确。** → 原子活跃租约是即时下限，评分取租约和上报值最大值；不单独依赖心跳。
- **风险：课程并发乱序提交 Kafka offset。** → 按 partition 维护连续完成水位，禁止越过未完成 offset；关闭时未完成消息不提交。
- **风险：16 个全局槽位被单个长课程短时占满。** → 使用有界批次 Worker/队列并保持课程间轮转；后续可在不改接口的前提下增加公平权重。
- **风险：1000 路在线突发短时压制离线批次。** → 混合验证按来源采集租约和进度，要求离线在突发期间或突发结束后的有界时间继续推进；本变更不承诺两类流量的机械平均份额。
- **风险：路由变化影响 OCR、FaceRec、ScreenDet、ASR 和 PPT。** → 公共选择器执行七算子跨能力回归，验证生命周期、共享容量、在线/离线和全满行为。
- **风险：删除旧镜像后失去立即镜像级回滚。** → 只在新版本完整验证后删除，删除前保存旧容器 inspect、镜像 digest 和 release 归属；Git、基础镜像和 BuildKit 缓存保留，可按记录重建。

## 迁移与回滚计划

1. 记录本地 dirty/untracked 边界和 `192.168.29.11` 当前完整容器/镜像 ID、revision、注册、租约和三卡基线，不覆盖历史 release。
2. 先以测试驱动实现公共选择器、VBas 上报和 Vision 并发，更新配置权威、测试、README、部署手册、OpenSpec 与 Harness。
3. 形成并推送新的完整 Git SHA；使用现有 BuildKit/镜像层缓存构建当前七算子和四平台同 revision 镜像，不执行 `--no-cache` 或宽泛 prune。
4. 构建全部成功并 inspect revision 后，保存旧容器/镜像精确账本，替换常驻容器；旧镜像在此阶段保留用于回滚。
5. 验证 29/29 healthy、21/21 注册、18 GPU、3 CPU PPT、7/7 Smoke 和 Stage45，再依次执行 20 个真实 `STUDENT_BEHAVIOR` 北向请求、独立的 `IMG-VBAS-1000` 在线并发以及 `MIXED-VBAS-OFF20-ONLINE1000` 重叠场景，并核验各自路由证据。
6. 任一门禁失败时停止新负载，保留新证据，按精确旧镜像和配置恢复旧容器，不删除旧版本。
7. 全部门禁通过后，按完整 ID 删除被替代的旧容器和旧镜像；不得删除基础镜像、BuildKit 缓存、volume、模型、Git、`/data/result` 或历史报告。清理后重验当前容器、注册、GPU 和结果完整性。
8. 使用新 seed、Campaign ID 和 write-once attempt 从阶段 0 重启 `run-milestone-2b-extreme-load-campaign`；既有 `d449dbad` attempt 保持只读。

## 待确认问题

无阻断性待确认项。`1024` 的真实容量收敛和 `worker.concurrency` 的生产取值留待本变更真实验证及后续容量报告决定，不影响按本设计实施路由正确性。

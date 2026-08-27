## 1. 基线、测试与变更边界

- [x] 1.1 记录本地分支、dirty/untracked 文件边界以及 `192.168.29.11` 当前容器完整 ID、镜像完整 ID/digest、Git revision、Compose 展开结果、三张 GPU 绑定、算子注册、活跃租约和磁盘占用基线，确保不覆盖用户文件或既有 Campaign 证据。
- [x] 1.2 为公共 Redis 注册表补充失败先行测试，复现排序第一实例在 `1/1024`、其他实例在 `0/1024` 时仍被选中的旧行为，并覆盖不同容量负载率、`reported_inflight` 高于活跃租约、失效心跳、`DRAINING` 和过期租约清理。
- [x] 1.3 补充并发原子租约测试，验证三个空闲等容量实例的首次三个租约分别落到三个实例、同负载持续轮询、高并发不超卖以及不同 capability/在线离线调用共享实例容量。
- [x] 1.4 为 VBas 补充 `running_batches` 心跳测试，覆盖批次进入、成功、失败、取消和归零，并验证日志不包含图片 Base64、完整请求体或完整检测结果。
- [x] 1.5 为 Vision Orchestrator 补充失败先行测试，证明现有课程级信号量会突破服务级 `16` 并发且 `[worker].concurrency` 未真正限制课程并发，同时覆盖 Kafka 同 partition 乱序完成和停机重放。

## 2. 公共算子实时负载路由

- [x] 2.1 重构 `RedisOperatorRegistry` 的租约 Lua 脚本，在一个原子操作中完成过期租约清理、候选过滤、有效负载计算、负载率比较、同负载轮询和新租约创建。
- [x] 2.2 按 `effective_inflight=max(active_lease_count, reported_inflight)` 计算实例负载，并使用交叉乘法比较 `effective_inflight/declared_capacity`，禁止把活跃租约和上报值相加或只按实例 ID 首次适配。
- [x] 2.3 按 capability 维护持久轮询游标；候选负载率相同时稳定排序后轮询，确保并发首批分散且健康实例不会持续饥饿。
- [x] 2.4 保留现有生命周期、模型就绪、TTL、续租、释放、归属和共享容量合同；容量耗尽时返回明确结果，不改变 Control Service 的北向课程接口。
- [x] 2.5 更新 Control Service 运维快照、指标和结构化日志，使租约记录可按 `task_id`、`batch_id`、`instance_id`、capability 和来源服务关联，并暴露活跃租约与上报负载差异。
- [x] 2.6 运行公共注册表单元、并发、容量和跨调用方回归测试，覆盖 VBas、OCR、FaceRec、ScreenDet、ASR 和 PPT 的既有注册与租约合同。

## 3. VBas 批次容量与负载上报

- [x] 3.1 将 VBas 的统一运行时 `inflight_provider` 绑定到 `BatchAdmissionController.running_batches`，使心跳、`/ops/status` 和 Worker 快照在稳定采样点一致。
- [x] 3.2 将 VBas 本地与部署配置统一为 `max_concurrent_requests=1024`、`MaxConcurrentBatches=1024`、`MaxQueueSize=0` 和 `declared_capacity=1024`，补充 TOML 注释与配置校验。
- [x] 3.3 验证一个最多包含 8 张图片的 HTTP batch 只占一个租约和一个 `running_batches` 计数，达到本地上限时直接过载拒绝且 `queued_batches` 始终为 `0`。
- [x] 3.4 运行 VBas 编译、导入、配置、学生/教师真实推理、注册心跳、过载和日志脱敏测试，确认不改变既有 HTTP 请求响应合同。

## 4. Vision 有界并发与安全消费

- [x] 4.1 将 Vision Orchestrator 的 `[vbas].max_batch_size` 设置为 `8`、`[vbas].max_concurrency` 设置为 `16`，并补充中文注释说明 `16` 是全部课程、流类型和区域轮次共享的服务级 VBas batch 并发。
- [x] 4.2 在服务启动时创建共享 VBas 信号量或固定批次 Worker，移除每次课程分析独立创建并发槽位的行为，并使用有界队列避免无界协程增长。
- [x] 4.3 实现批次租约完整生命周期：取得全局槽位后申请租约，按返回的 `service_url` 调用实例，在成功、失败、超时和取消路径精确释放；容量不足时有界等待，实例过载时释放后重新选择。
- [x] 4.4 让 `[worker].concurrency` 真正限制同时执行的视觉课程命令，并保证一个课程的不可恢复批次错误只取消该课程尚未开始的批次，不影响其他课程。
- [x] 4.5 按 Kafka partition 维护在途 offset 与连续完成水位，禁止提交越过未完成消息；停机时停止领取、在关闭期限内排空，未完成消息保持可重放并依赖节点幂等事实收敛。
- [x] 4.6 运行单课程三实例、多课程共享 `16` 槽位、课程 Worker 上限、容量等待、过载重选、乱序 offset、优雅停机、重放和结果结构回归测试。

## 5. 配置、文档与 Harness

- [ ] 5.1 更新本地 `config.toml`、远端 Compose/部署变量和配置展开测试，固定 VBas `1024/1024/0` 与 Vision `8/16`，并证明运行容器实际读取的值与平台注册值一致。
- [x] 5.2 更新相关 README、部署手册和算法功能调度平台设计文档，说明实时负载路由、共享容量、服务级 Vision 并发、无本地队列、Kafka offset 安全和 A 服务零改动边界；保留既有历史架构图与历史结论。
- [x] 5.3 更新 `LOAD-007`、里程碑 2B Campaign 用例和 Harness 断言，废止“允许排序第一实例长期独占”的后续范围，并保留旧 `d449dbad` attempt 及历史 Text Analysis 证据为只读。
- [x] 5.4 记录本地自动化测试命令、输入、退出码、关键断言和证据路径；执行 `openspec validate balance-operator-routing-by-live-load --strict`、`git diff --check` 及相关服务测试后形成规范中文提交并推送完整 Git SHA。

## 6. 192.168.29.11 缓存构建与发布预检

- [ ] 6.1 在 `192.168.29.11` 拉取已推送 SHA，确认基础镜像、模型、共享目录和磁盘空间可用，保存旧版本精确回滚账本；禁止提前删除旧容器、旧镜像或构建缓存。
- [ ] 6.2 复用现有 BuildKit 和镜像层缓存构建七算子与四平台共 11 个同 revision 新镜像，不使用 `--no-cache` 或宽泛 prune，并逐个 inspect 镜像 revision 标签与 digest。
- [ ] 6.3 任一构建或 revision 校验失败时保留当前运行版本并记录 Harness；全部构建通过后才替换常驻容器，同时继续保留旧镜像用于回滚。
- [ ] 6.4 校验 29/29 容器 healthy、21/21 算子实例注册、18 个 GPU 算子实例、3 个 CPU PPT 实例、三张卡设备绑定、7/7 Smoke、Stage45、共享目录和日志基线。
- [ ] 6.5 预检 `vbas-gpu0/1/2` 分别绑定 GPU 0/1/2，均注册学生/教师能力并报告 `declared_capacity=1024`，VBas 运行值为 `1024/1024/0`，Vision 运行值为 `8/16`；任一漂移均停止发布验证。

## 7. 二十任务真实均衡验证

- [x] 7.1 编写可重复的真实负载脚本，并发向 `POST /api/course-jobs` 提交 20 个只替换唯一 `task_id` 的 `STUDENT_BEHAVIOR` 请求，严格保留冻结的 S 视频 URL、空教师/PPT 路径、前后排坐标、`student_count=70` 和 `asr_options=null`。
- [ ] 7.2 验证 20 个请求均按既有合同新建或幂等受理，PostgreSQL/Kafka 不产生重复逻辑任务，且每个 `task_id` 均可通过现有查询接口取得课程与节点状态。
- [ ] 7.3 采集 Control 活跃租约时序，证明首次三个并发租约分别归属 `vbas-gpu0/1/2`，并按 `task_id/batch_id/instance_id` 汇总整个窗口的租约创建、续租、释放和容量不足事件。
- [ ] 7.4 采集三个 VBas 容器的接受、过载和终态日志，并与 Control 租约逐批关联；要求三个实例均处理真实批次，且其他实例持续空闲时不得由固定实例长期独占。
- [ ] 7.5 同步采集 `nvidia-smi` 进程、显存、利用率和宿主 PID 映射作为 GPU 活跃补充证据，但不得仅凭 GPU 进程判定路由通过。
- [ ] 7.6 等待 20 个任务达到预期终态，核对现有 `STUDENT_BEHAVIOR` 结果结构、中文失败原因、活跃租约归零、Vision 在途归零及 Kafka lag 收敛；失败时记录具体阶段，不把部分成功写成全部通过。
- [ ] 7.7 以相同请求字段执行 A 服务兼容回归，确认提交/查询路径、字段名、字段可选性、响应结构、整数状态和异步语义均无变化，A 服务不需要传入实例、租约、batch 或 GPU 信息。

## 8. 千路在线 VBas 并发验证

- [ ] 8.1 在离线 20 任务验证结束并收敛后，预检负载机文件句柄、可用端口、连接池和网络能力，并确认 Online Gateway 运行配置为 `max_connections=2048`、`max_keepalive_connections=512`，三个 VBas 实例健康空闲且注册容量一致。
- [x] 8.2 扩展既有 `IMG-VBAS-1000` 用例，同时向 `POST /api/online/vbas/analyze` 发起 1000 个唯一 `ImageId` 和链路标识的合法真实单图请求，固定 `stream_type=student`，不直连 VBas、不使用多图请求。
- [ ] 8.3 断言 1000 个请求全部取得租约并返回既有业务成功响应，分别统计 `50301`、`50000`、连接池错误和超时；任何非零失败必须保留中文原因且不得写成全部通过。
- [ ] 8.4 采集 Online Gateway 按实例调用增量、Control 租约申请/取得/释放时序、三个 VBas 容器唯一 `ImageId` 日志和 `nvidia-smi` 补充证据，证明首次三个租约覆盖三实例且不存在固定实例长期独占。
- [ ] 8.5 停止在线负载后核对已取得租约数、实例调用数和成功响应数，等待活跃租约与三个实例 `running_batches` 归零；负载机未形成 1000 个同时在途请求时将执行标记为测试环境无效并重新准备，不判定平台通过。

## 9. 二十离线任务与千路在线请求重叠验证

- [x] 9.1 增加专用 `MIXED-VBAS-OFF20-ONLINE1000` 用例，以新的 20 个唯一 `task_id` 提交真实 `STUDENT_BEHAVIOR` 任务，并冻结与独立离线验证相同的视频、区域和人数参数。
- [x] 9.2 实现重叠门槛：持续读取 Control 租约和课程节点进度，只有在 Vision Orchestrator 已持有真实 VBas 活跃租约且三个实例均出现离线批次后，才同时释放 1000 个唯一单图在线请求；超时未形成门槛时失败关闭。
- [ ] 9.3 验证在线与离线通过同一实例租约集合和声明容量评分，按来源统计每实例活跃租约、取得、拒绝和释放，断言实例总租约不超卖且三实例均有真实工作。
- [ ] 9.4 验证 1000 个在线请求全部返回既有业务成功响应，在线请求不存在固定实例独占，并确认 20 个离线课程在突发期间或结束后的有界时间继续取得租约和推进，不因在线突发进入错误终态。
- [ ] 9.5 等待 20 个离线任务达到预期终态，核对在线响应、课程节点、活跃租约、VBas `running_batches`、Vision 在途批次和 Kafka 积压全部收敛，并按调用来源生成中文 Harness 证据。

## 10. 验收、回滚与精确清理

- [ ] 10.1 任一健康、注册、真实推理、离线、在线或混合路由、结果或收敛门禁失败时停止新负载，保留失败证据，并按旧容器/镜像完整 ID 和配置账本恢复旧发布；不得删除旧版本。
- [ ] 10.2 全部门禁通过后，先核对旧版本账本与当前 Docker inspect，再只按完整容器 ID 和镜像 ID 删除已被替代的旧容器与旧镜像，记录清理前后空间；保留基础镜像、BuildKit 缓存、volume、模型、Git、`/data/result` 和历史报告。
- [ ] 10.3 清理后重新验证当前 revision、容器健康、21 个注册实例、三卡 VBas、7/7 Smoke、共享目录、结果和历史证据完整性，并把最终报告与命令证据写入 Harness。
- [ ] 10.4 使用新 seed、Campaign ID 和 write-once attempt 从阶段 0 恢复 `run-milestone-2b-extreme-load-campaign`，不得改写既有只读 attempt。

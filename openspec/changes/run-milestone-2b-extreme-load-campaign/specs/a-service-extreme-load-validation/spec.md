## ADDED Requirements

### Requirement: 当前七算子三卡拓扑必须通过预检
负载 Campaign 系统 SHALL 在加压前验证同一完整 Git SHA 下的七类算子、21 个算子实例、18 个 GPU 实例、3 个 CPU PPT Slice 实例、四个平台服务以及 PostgreSQL、Kafka、Redis 和 MongoDB 都已就绪。当前路由和部署权威 MUST 不包含 `text_analysis`、`PPT_KEYWORDS` 或 `COURSE_OVERVIEW`。

#### Scenario: 完整拓扑允许开始加压
- **WHEN** 21 个算子实例的 revision、注册、租约接口、GPU/CPU 归属和 7/7 Smoke 全部符合当前拓扑权威
- **THEN** Campaign 系统发布预检证据并允许进入单请求基线阶段

#### Scenario: 拓扑或 revision 漂移禁止加压
- **WHEN** 任一必需实例缺失、GPU 归属错误、镜像 revision 不同或发现当前 Text Analysis 实例
- **THEN** Campaign 系统 SHALL 在生成业务负载前失败关闭并记录中文原因

### Requirement: A 服务模拟器必须从平台北向接口加压
A 服务模拟器 SHALL 从目标服务器之外的可识别负载主机运行，且只能访问 `control-service:18100` 和 `online-gateway-service:18103`。业务泳道验证 MUST 不直连算子、不直改 PostgreSQL 任务终态也不调用 Repository 完成方法。

#### Scenario: 通过北向接口完成泳道
- **WHEN** 模拟器提交离线任务、轮询节点状态、发送在线图片和建立 ASR WebSocket
- **THEN** 所有业务请求 SHALL 只经过两个北向端口，且报告保留请求端点和追踪标识

#### Scenario: 直连算子证据不能补足全链路
- **WHEN** 某用例只存在算子 HTTP/WS 调用成功证据
- **THEN** Campaign 聚合器 SHALL 将对应 A 服务全链路用例判定为未完成而非通过

### Requirement: 媒体源和局域网下载能力必须先建立基线
Campaign 系统 SHALL 在提交短媒体突发或真实长课阶梯之前，从 `192.168.29.11` 对 `192.168.29.12:5555` 执行 1、3、10、30 并发下载基线。基线 MUST 记录单文件速度、总吞吐、建连耗时、失败率、源端文件服务资源和目标机入站网络，并与 Control、Orchestrator、ffmpeg 和算子耗时分开报告。

#### Scenario: 并发下载基线完成
- **WHEN** 目标机对同一批可追溯 T/S/P fixture 完成 1、3、10、30 并发下载
- **THEN** 报告 SHALL 给出各阶梯的源端、局域网和目标机入站指标，并允许后续离线 Campaign 引用该基线做瓶颈归因

#### Scenario: 媒体源先达到上限
- **WHEN** 文件服务或局域网在算子未达到稳定吞吐前已出现持续吞吐封顶、超时或失败
- **THEN** 端到端结果 SHALL 保留为媒体源/网络限制，不得用它宣称 GPU 或算子已达到最大吞吐

### Requirement: Campaign 用例必须可重放
Campaign 系统 SHALL 为每条用例维护唯一 ID、阶段、前置条件、负载参数、随机种子、fixture 摘要、预期、超时、停止条件、清理和证据路径。相同发布的每个必需 ID MUST 恰好执行一次。

#### Scenario: 相同种子重放负载
- **WHEN** 操作者使用相同 Campaign ID、用例配置、随机种子和 fixture manifest 在新 release 中重跑
- **THEN** 模拟器 SHALL 产生相同的请求分布和可追溯的新 `task_id` 集合

#### Scenario: 重复或缺失用例不得聚合通过
- **WHEN** 必需用例 ID 重复执行、没有执行或缺失原始证据
- **THEN** 总报告 SHALL 失败关闭并列出精确 ID

### Requirement: 离线负载必须分离调度突发与真实长视频
Campaign 系统 SHALL 使用短媒体执行 100、300、1000 个唯一任务的提交突发，使用 45–60 分钟真实 T/S/P 视频执行 3、6、12、24、36 节活动课程阶梯。离线集合 MUST 覆盖 `PPT`、`ASR`、`TEACHER_BEHAVIOR`、`STUDENT_BEHAVIOR` 单项与混合组合。

#### Scenario: 短媒体千任务突发
- **WHEN** 模拟器以 1000 个唯一 `task_id` 并发调用 `POST /api/course-jobs`
- **THEN** 报告 SHALL 分别给出接收吞吐、HTTP 结果、Outbox 发布、Kafka lag、DAG 初始化、节点排队和最终排空结果

#### Scenario: 真实长课阶梯
- **WHEN** 活动长课从 3 逐级增加到 36 且每级都重新检查资源护栏
- **THEN** 系统 SHALL 记录每级的下载、ffmpeg、队列等待、算子推理、租约续约、临时/持久目录增长和任务终态

#### Scenario: 阶段一单泳道与长课逐级门禁
- **WHEN** Campaign 准备执行阶段一离线负载
- **THEN** 系统 SHALL 使用独立于阶段零和阶段二的 PPT、ASR、教师行为、学生行为单泳道用例，四条全部通过后才允许执行 3 节长课，并按 `3 -> 6 -> 12 -> 24 -> 36` 的前置依赖逐级解锁

#### Scenario: 磁盘警戒线阻止更高阶梯
- **WHEN** 当前长课阶梯结束后剩余空间已低于配置的警戒线
- **THEN** Campaign 系统 SHALL 禁止启动下一阶梯，继续排空当前任务并把用例标记为护栏中止

#### Scenario: 长课提交前投影跨越警戒线
- **WHEN** 当前可用空间减去本档课程数乘三路长课 fixture 总字节后低于 15% 或 150 GiB 警戒线
- **THEN** Campaign 系统 MUST 在产生任何本档请求前将该档标记为阻断，记录当前空间、预计输入、投影空间和阈值，不得先提交后依赖运行中护栏补救

#### Scenario: 单任务泳道忽略未请求任务字典
- **WHEN** PPT-only、ASR-only、教师-only 或学生-only 查询返回一个已请求任务和三个 `status=0` 的未请求任务
- **THEN** Campaign 终态轮询 SHALL 排除未请求项，只根据至少一个已请求项判断成功、失败或继续等待；四项全部为 `0` 时不得提前成功

### Requirement: 短媒体基线必须验证输入有效性和确定性终态
Campaign 系统 SHALL 使用同一真实课程、同一时间窗口的短 T/S/P 片段执行离线基线，并冻结
URL、字节数、时长和 SHA-256。教师短片段 MUST 包含可识别的真实人声；三路 MUST 完成
ffprobe、完整解码和安全末端抽帧验证。PPT 与视觉单任务遇到确定性处理错误时 MUST 进入失败
终态并允许消费循环继续处理后续命令，不得永久停留在运行中或使整个后台循环退出。

#### Scenario: 无有效人声的 ASR fixture
- **WHEN** 离线 ASR 返回“音频文件为空或未检测到任何人声”，且音频探针证明短教师 fixture 没有有效授课语音
- **THEN** 当前基线 SHALL 归因为 fixture 前置条件失败，并使用新的有效短片段和全新 attempt 重跑，不得把该结果写成 ASR 模型容量不符合

#### Scenario: PPT 正常 EOF 恰好达到最小帧数
- **WHEN** PPT 视频正常结束且已处理采样帧数恰好等于 `min_frames_ok`
- **THEN** PPT Slice SHALL 立即消费正常 EOF 并按成功终态处理，不得等待队列超时或误报网络码流异常

#### Scenario: 视觉确定性失败后继续消费
- **WHEN** 一个视觉命令因媒体抽帧或分析错误写入失败终态，且先前发布的进度事件随后到达
- **THEN** Vision Orchestrator SHALL 聚合失败任务并提交命令，Orchestrator SHALL 幂等提交已终态节点的滞后进度，后续视觉命令仍可继续执行

#### Scenario: 单任务分析错误不吞掉基础设施错误
- **WHEN** 视觉分析边界出现媒体缺失、非法策略、VBas 结果字段缺失或证据文件缺失
- **THEN** `ValueError`、`TypeError`、`KeyError`、`FileNotFoundError` SHALL 写入当前节点失败终态并允许消费后续命令；进度落库、事件发布、终态持久化或 Kafka 提交异常 MUST 继续失败关闭，不得伪装成任务失败

#### Scenario: 修复后创建全新发布 attempt
- **WHEN** 某一阶段 0 attempt 已因实现或 fixture 缺陷产生失败或部分证据
- **THEN** 系统 MUST 保留原证据，使用包含修复的新完整 Git SHA 重建 11 个镜像，并以新 seed、Campaign ID 和 write-once attempt 从阶段 0 重跑

#### Scenario: PPT 运行节点尚未持久化异步身份
- **WHEN** PPT 节点已经进入 `RUNNING`，但异步提交返回的 `task_id` 和 `operator_task_id` 尚未写入节点进度
- **THEN** Orchestrator 对账循环 SHALL 从所属任务类型读取持久 `task_id`，按 `ppt-node-{node_id}` 确定性恢复算子身份并继续 manifest 对账，不得终止全部后台循环或无限跳过；manifest、数据库或已持久化身份的真实错误仍 MUST 失败关闭

#### Scenario: PPT 终态回调与对账并发完成
- **WHEN** PPT 终态回调和对账循环并发处理同一运行节点，一方已率先持久化与回调一致的终态
- **THEN** 后到一方 MUST 在状态和持久化终态载荷均一致时把它视为幂等重复并继续运行，不得因 `60 -> 60` 或 `70 -> 70` 停止 Orchestrator 后台循环；完成载荷 MUST 核对 `path/count/manifest_path/dynamic_segments`，失败载荷 MUST 核对 `reason`；若竞争后终态或载荷与回调不一致，MUST 以可识别冲突继续失败关闭

### Requirement: 幂等、追加任务类型与优先级必须在压力下保持语义
Campaign 系统 SHALL 对同一 `task_id` 执行 30、100、300、1000 次并发相同提交，并验证分批追加 `task_types`、已完成结果复用和冲突媒体请求。Campaign 还 SHALL 在堆积的 `NORMAL` 后注入 `URGENT`，验证只对未领取节点插队。Control 课程查询的节点字典 MUST 从 PostgreSQL 节点事实返回可空 `claimed_at` 和 `started_at`，Campaign MUST 使用它们证明领取和开始顺序。

#### Scenario: 千请求幂等竞争
- **WHEN** 1000 个并发请求带有相同 `task_id`、相同 `task_types` 和相同媒体字段
- **THEN** 平台 MUST 只保留一组逻辑任务/节点事实，所有响应必须可解释且不产生重复结果

#### Scenario: 负向混合流量区分同步拒绝与异步失败
- **WHEN** 负向比例同时包含缺少路径、未知任务类型、404/超时媒体和非法区域请求
- **THEN** 缺少路径与未知类型 SHALL 同步拒绝且不创建任务或 Outbox，404/超时媒体与非法区域 SHALL 在接受后进入对应异步失败终态，正常任务 SHALL 全部成功，最终活动队列、Outbox、Kafka lag 和租约 SHALL 排空

#### Scenario: 超时媒体由受控端点证明
- **WHEN** Campaign 准备执行包含超时媒体的负向比例用例
- **THEN** write-once plan SHALL 固化受控超时 URL，预探测 MUST 先确认同 origin 的 `/healthz` 为 `200/ok`，再以 2 秒 Range 请求证明已连接后的 `ReadTimeout` 才允许提交；`ConnectTimeout`、`WriteTimeout`、`PoolTimeout`、快速 404、连接失败或未超时响应 SHALL 使该用例零请求阻断

#### Scenario: 异步负向失败必须命中对应节点
- **WHEN** 404/读超时媒体或非法区域请求被 Control 接受并进入失败终态
- **THEN** Campaign SHALL 查询课程任务事实，验证请求中对应 `task_type` 的状态为 `70` 且该任务类型下至少一个节点状态为 `70`；查询证据缺失时阻断，失败落在其他任务类型或没有失败节点时使用例失败

#### Scenario: 终态父任务的残留节点不计入活动队列
- **WHEN** 历史节点状态仍为 `10`–`50`，但所属课程任务类型已经进入 `60/70/80` 终态
- **THEN** `/ops/queues` SHALL 保留数据库历史事实但不把这些节点计入活动队列深度

#### Scenario: URGENT 不抢占运行节点
- **WHEN** 300 个 `NORMAL` 任务中已有部分节点运行，随后提交 30 个 `URGENT`
- **THEN** `URGENT` SHALL 优先于尚未领取的 `NORMAL` 被领取，而已运行节点不被取消或重复执行

### Requirement: A 服务查询必须覆盖稳定轮询与惊群突发
Campaign 系统 SHALL 以 50、100、300、1000 QPS 查询运行课程的全部节点状态和已完成的大 ASR 结果。正常模型 MUST 带轮询抖动，并另设无抖动同时查询的惊群用例。

#### Scenario: 带抖动的持续轮询
- **WHEN** 100 个活动任务按 2 秒或 5 秒周期并附加随机抖动轮询
- **THEN** 报告 SHALL 记录查询 QPS、P50/P95/P99、响应大小、PostgreSQL 负载与各节点状态的单调合法迁移

#### Scenario: 千 QPS 惊群
- **WHEN** 模拟器在无抖动的短窗口内生成 1000 QPS 查询
- **THEN** 系统 SHALL 记录成功、限流、超时和内部错误的独立分布，并在停止突发后验证 Control Service 恢复就绪

### Requirement: 在线图片必须按单图单请求执行极限阶梯
Campaign 系统 SHALL 分别通过 `/api/online/vbas/analyze`、`/api/online/face/recognize`、`/api/online/image-quality/detect` 和 `/api/online/ocr/recognize` 执行 1、3、10、30、60、100、256、512、1000 并发阶梯。常规请求 MUST 为单图且不大于 5 MiB，另设 49 MiB 与超过 50 MiB 的边界用例。

#### Scenario: 四类图片单算子阶梯
- **WHEN** 任一在线图片接口从单请求逐级增加到 1000 并发
- **THEN** 每级报告 SHALL 给出请求速率、成功/拒绝/超时/内部错误、P50/P95/P99、实例选择、inflight、租约释放、GPU/CPU/内存和容器重启计数

#### Scenario: 多 S 流持续帧请求
- **WHEN** 1000 路逻辑 S 流在 5 秒间隔下持续生成单帧 VBas 请求
- **THEN** 模拟器 SHALL 产生约 200 RPS 的可追溯负载，并验证请求级实例路由而不执行 RTSP 接入或抽帧

#### Scenario: 超过图片上限
- **WHEN** A 服务发送解码后超过 50 MiB 的 Base64 图片
- **THEN** Online Gateway SHALL 按已定义边界拒绝请求，不获取算子租约且不在日志写入 Base64

### Requirement: Online Gateway 必须把在线容量上限交给租约和算子
Online Gateway SHALL 使用 `config.toml` 中可配置的出站连接池承接至少 1000 个常规在线图片并发，不得由当前 `http.max_connections=100` 形成人为业务上限。里程碑 2B 三卡发布和压测配置 MUST 使用 `max_connections=2048`、`max_keepalive_connections=512` 和有界 `pool_timeout_seconds`。有可用租约时才能调用算子，无可用租约时 MUST 快速返回已定义的 `50301`。

#### Scenario: 千并发不被 Gateway 连接池提前截断
- **WHEN** A 服务以 1000 并发发送 0.5–5 MiB 的合法单图请求
- **THEN** Online Gateway SHALL 完成请求验证和租约决策，不得因自身连接池上限/等待超时产生 `50000`，有租约请求调用算子，无租约请求返回 `50301`

#### Scenario: 分离 Gateway、租约和算子容量
- **WHEN** 在线图片阶梯完成并生成容量报告
- **THEN** 报告 SHALL 分别给出 Gateway 承接吞吐、租约申请/拒绝、各算子实测稳定吞吐和过载拒绝，并据此收敛后续 `declared_capacity`

### Requirement: 实时 ASR 必须按真实时钟执行会话阶梯
Campaign 系统 SHALL 通过 `/api/online/asr/stream` 执行 1、10、24、30、60、90、150 会话阶梯，每个会话按真实采样速率发送音频，不得一次性灌入整段媒体来伪造实时压力。

#### Scenario: 声明容量内的三十会话
- **WHEN** 30 个 ASR WebSocket 会话持续推送实时音频
- **THEN** 系统 SHALL 验证会话粘性、字幕不串流、音频处理不持续落后于实时时钟、正常结束后租约释放

#### Scenario: 一百五十会话过载
- **WHEN** 150 个并发会话超过三实例声明总容量
- **THEN** 系统 SHALL 区分已接受和被拒绝/中断会话，不超卖租约，并在停止新会话后恢复接受能力

#### Scenario: 断线与重连
- **WHEN** 负载生成器主动中断部分会话并重新连接
- **THEN** 旧会话租约 SHALL 最终释放，新会话取得独立追踪和实例粘性，不得继续返回旧会话字幕

### Requirement: 人脸库必须在三实例并发下保持一致
Campaign 系统 SHALL 使用 500、1000、5000 人数据集覆盖人脸新增、批量新增、查询、搜索、删除和识别。Online Gateway 对管理接口 SHALL 固定转发到单一 FaceRec 管理实例，`/face/recognize` SHALL 通过租约在三个 FaceRec 识别实例间路由。三个识别实例 MUST 通过共享 MongoDB 观察到一致的人员事实；`save_person_photo=false` 时 MUST 不保存人脸原图。

#### Scenario: 五千人并发入库与识别
- **WHEN** A 服务分批并发写入 5000 人，同时执行查询、搜索和识别
- **THEN** 所有经单一管理实例成功写入的人员 SHALL 在三个租约路由的识别实例上可识别，且没有重复、部分更新或已删除数据复活

#### Scenario: 人脸管理与识别容量分开报告
- **WHEN** 人脸库阶梯执行完成
- **THEN** 报告 SHALL 分别给出单管理实例吞吐、三识别实例吞吐和 MongoDB 一致性，不得声称管理请求已在三实例负载均衡

#### Scenario: 禁止保存原图
- **WHEN** 人脸库以 `save_person_photo=false` 执行整个数据集的新增、识别和删除
- **THEN** 报告 SHALL 验证功能与 embedding 不受影响，且容器、MongoDB、日志和持久目录不存在人脸原图

### Requirement: Campaign 必须执行混合、过载和长稳负载
Campaign 系统 SHALL 执行日常、高峰和极限三种混合档，且在实测稳定容量的 70%–80% 运行至少 4 小时长稳。如资源和时间允许，还 SHALL 执行 8 小时长稳档，但不得用 8 小时档缺失覆盖 4 小时必需档。

#### Scenario: 极限混合负载
- **WHEN** 36 节离线长课、300 路 S 流、1000 在线图片并发、150 路 ASR 会话和 1000 QPS 查询按阶梯重叠
- **THEN** 报告 SHALL 清晰区分成功、预期过载拒绝、超时、未定义错误和护栏中止，并在停止加压后验证平台恢复就绪与队列排空

#### Scenario: 四小时长稳
- **WHEN** 平台在已确定稳定容量的 70%–80% 运行 4 小时
- **THEN** 系统 MUST 不出现 OOM、未预期容器重启、持续租约泄漏、跨任务数据污染、不可排空队列或无界磁盘增长

### Requirement: 故障注入必须验证实例、GPU 组、平台和中间件恢复
Campaign 系统 SHALL 在持续受控负载中分别停止一个算子实例、一张 GPU 上的六个算子实例，并逐个重启四平台服务、Kafka 和 Redis。每次注入 MUST 使用精确容器身份、完成预期验证、恢复原状后才能进入下一次。

#### Scenario: 单 GPU 算子集群停止
- **WHEN** 精确停止 GPU1 上的 asr_offline、asr_online、ocr、vbas、facerec 和 screen_det 六个实例
- **THEN** TTL 过期后新请求 SHALL 只路由到 GPU0/GPU2 的可用实例，不得向已离线实例发放新租约，恢复 GPU1 后六实例必须重新健康注册

#### Scenario: Kafka 重启期间 Outbox 恢复
- **WHEN** 持续提交离线任务时受控重启 Kafka
- **THEN** 已接受任务的 Outbox 事实 SHALL 保留，Kafka 恢复后可重发/消费且不创建重复 DAG 或丢失任务

#### Scenario: Gateway 重启中断 WebSocket
- **WHEN** 实时 ASR 会话存活时受控重启 Online Gateway
- **THEN** 报告 SHALL 如实记录会话中断，验证客户端重连、旧租约最终释放和新会话恢复，不得将其表达为无感迁移

#### Scenario: attempt 根必须反解同一发布身份
- **WHEN** Fault Adapter 接收 `<tag>/<sha>/attempts/<attempt-id>` 形式的 write-once attempt 根，或显式兼容的 direct `<tag>/<sha>` release 根
- **THEN** 系统 MUST 严格反解 release tag、完整 Git SHA 和 attempt 边界，只接受 `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}` 形式的 attempt ID，拒绝其他目录形状或身份不一致的路径，并以 `local_release_layout=attempt|legacy_direct` 记录实际分支，不得因固定父目录层数而把 attempt 误判为 release

#### Scenario: 本地与远端维护锁使用独立绑定
- **WHEN** Campaign 控制器在 Mac 运行，并通过 SSH 对 `192.168.29.11` 执行一个故障 case
- **THEN** Fault Adapter MUST 使用专用 `_LocalCampaignLockGuard` 获取并全程持有当前 attempt 根下的 `.campaign-fault.lock`，要求该文件为当前用户所有的 `0600` 单链接且内容绑定 schema、Campaign ID 和 attempt root，并在每次动作复核目录、inode、权限与 mtime/ctime；`delegated_lock_holder_pid` 和 `delegated_lock_path` MUST 只描述目标机 canonical 锁，每次远端 Docker 动作仍 MUST 同时通过本地 lock probe 和 semantic probe SSH challenge 校验，不得要求同一个 PID/path 同时在两台主机成立，结果 MUST 标记 `maintenance_lock_binding=local_attempt_and_remote_canonical`

#### Scenario: 故障前审计发现结构性阻断
- **WHEN** 已有 attempt 只完成部分阶段 0 case，预执行审计证明阶段 5 的 attempt root 或跨主机锁绑定必然失败
- **THEN** Campaign MUST 停止启动后续 case、保留全部既有 plan/case/指标且不补写未执行结果；修复后 MUST 使用新完整 Git SHA 重建 11 个镜像，并以新 seed、Campaign ID 和 write-once attempt 从阶段 0 重跑

### Requirement: 资源护栏必须优先于负载目标
Campaign 系统 SHALL 至少每 5 秒发起宿主机、Docker、GPU、磁盘、Kafka lag、任务队列和容器重启指标采集；在线图片突发阶段 MUST 每 0.5–1 秒采集实例 `inflight`、活跃租约和容量峰值，并使用 Gateway 实例级请求、租约申请/拒绝/释放累计指标的阶段前后差值覆盖短租约。Kafka lag MUST 作为独立于 Control HTTP 的 `kafka_lag` 采集面，使用独立的 15–30 秒命令超时，默认 20 秒；其他探针不得因此放大超时。任一红线触发时 MUST 立即停止新负载，保留证据并执行精确恢复，不得为了完成目标并发而继续加压。

#### Scenario: 短租约由高频峰值和累计差值共同取证
- **WHEN** 在线图片请求在 5 秒常规采样周期内已完成租约申请、调用和释放
- **THEN** Campaign 系统 SHALL 使用 0.5–1 秒峰值采样和阶段前后累计差值证明实例调度，不得因 5 秒快照未观测到租约而判定调度未发生

#### Scenario: 中途护栏事件不得被恢复后的 CLEAR 覆盖
- **WHEN** 用例运行窗口内任一样本出现 `WARNING` 或 `STOP`，后续精确恢复样本重新变为 `CLEAR`
- **THEN** 运行时汇总 MUST 保留窗口内最高严重度及其去重原因，当前用例 MUST 标记为阻断；恢复后的 `CLEAR` 只证明现场恢复，不能把规范结果改写为通过

#### Scenario: Kafka lag 瞬时命令失败在同一采样内有限重试
- **WHEN** 一次 Kafka consumer group 快照命令因瞬时超时失败，但在配置的有限尝试次数内恢复
- **THEN** 独立 `kafka_lag` 采集面 SHALL 使用默认 20 秒且限制为 15–30 秒的独立超时，以单次 all-groups 快照汇总 Orchestrator、视觉事件和 Vision Orchestrator 三个必需消费组，并继续当前采样；尝试次数 MUST 不超过 2 次且默认 2 次，默认重试间隔 MUST 为 0.25 秒；全部尝试失败、任一必需组缺失或输出不可证明时 MUST 锁存 `STOP`

#### Scenario: Kafka lag 失败证据独立可发现
- **WHEN** `kafka_lag` 采集面在全部尝试后仍失败，后续收尾采样重新成功
- **THEN** 当前用例 MUST 保持 `STOP`，系统 MUST 写入仅含 case、时间、采集面、异常类型和尝试次数的脱敏失败 JSON，并通过独立 `failure_evidence` 路径列表公开；失败事件 MUST NOT 混入成功样本的 `sample_evidence`

#### Scenario: 磁盘达到红线
- **WHEN** 宿主机或关键数据目录所在文件系统剩余空间低于 100 GiB 或 10%
- **THEN** Campaign 系统 MUST 立即停止新请求/会话，发布红线证据，保留 `/data/result` 并进入受控排空/恢复

#### Scenario: 负载机先达到上限
- **WHEN** 负载机的 socket、CPU、内存或网络已无法产生指定负载
- **THEN** 报告 SHALL 将该阶梯标记为负载机限制，不得把结果归因于调度平台，并允许通过多 worker 分片重跑

### Requirement: 验收报告必须分离稳定容量与预期过载
Campaign 聚合器 SHALL 先使用单请求和单泳道结果建立基线，再按稳定容量内、过载和护栏中止三种性质评估后续阶梯。报告 MUST 包含成功率、业务错误、HTTP/WS 错误、P50/P95/P99、吞吐、队列等待、Kafka lag、inflight/租约、GPU/CPU/内存/磁盘和容器重启。

#### Scenario: 稳定容量内达到最低工程线
- **WHEN** 负载不超过实测稳定容量
- **THEN** 非预期 5xx/连接失败率 SHALL 不高于 0.1%，P95 SHALL 不高于对应基线 3 倍，P99 SHALL 不高于 5 倍，且不存在任务丢失、重复结果、容量超卖或未预期容器重启

#### Scenario: 过载按设计拒绝
- **WHEN** 负载明确超过实测稳定容量
- **THEN** 已定义的限流/过载响应可被判为预期通过，但无界排队、未定义内部错误、租约超卖、无法恢复就绪或无法排空 SHALL 使用例失败

#### Scenario: 新 Campaign 不覆盖原有门禁
- **WHEN** 极限负载 Campaign 全部执行完成
- **THEN** 最终里程碑结论仍 MUST 需要同一当前 SHA 下的 217 条反例、26 条压力/恢复用例和 6 项 B 级复核，任一缺失或失败都不得发布“全部符合”结论

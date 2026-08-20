## ADDED Requirements

### Requirement: 活跃租约是平台分发占用的权威事实
Control Service SHALL 只用实例当前有效的 Redis 活跃租约数判断平台是否还能分发新工作；心跳 `reported_inflight` SHALL 保留用于观测和差异告警，但 SHALL NOT 阻止新租约。

#### Scenario: 短请求释放后心跳尚未刷新
- **WHEN** 一个短请求已释放租约但算子上一轮心跳仍报告较高 `reported_inflight`
- **THEN** 释放出的槽位 SHALL 立即可被新的原子租约使用

#### Scenario: 心跳发现未归因请求
- **WHEN** `reported_inflight` 大于可归因活跃租约数
- **THEN** 运维数据 SHALL 显示差异但分配器 SHALL NOT 把差异伪造成租约或任务

### Requirement: 容量租约分配保持原子和共享
Control Service SHALL 在一次 Redis 原子操作中清理失效租约、筛选心跳有效且 `ONLINE`、模型就绪的实例、检查实例共享容量并写入新租约；并发调用 SHALL NOT 超过实例可分发容量。

#### Scenario: 并发争抢最后一个槽位
- **WHEN** 多个调用方同时申请同一共享实例的最后一个槽位
- **THEN** 恰好一个申请 SHALL 成功，其余申请 SHALL 得到容量不可用

#### Scenario: 不同能力争抢同一实例
- **WHEN** 两种 capability 同时申请同一多能力实例的最后一个槽位
- **THEN** 两种申请 SHALL 竞争同一租约集合而不是各自拥有一个最后槽位

### Requirement: 租约记录包含时间和可选工作上下文
每个活跃租约 SHALL 在 Redis 中保存 `lease_id`、`instance_id`、`capability`、`service_url`、`acquired_at`、`expires_at` 和可选 `work_context`；出现上下文时 `source_service`、`work_type`、`work_id` SHALL 必填，`task_id`、`node_id`、`item_id`、`trace_id` SHALL 可选。

#### Scenario: 申请时已知工作身份
- **WHEN** Online Gateway、图片工作项或 Vision Orchestrator 在申请租约时提供合法 `work_context`
- **THEN** Control Service SHALL 使用 Redis 时间在同一租约记录中原子保存获取时间、过期时间和上下文

#### Scenario: 上下文包含业务正文
- **WHEN** 调用方尝试在 `work_context` 中提交未声明字段、Base64、媒体内容或识别文本
- **THEN** Control Service SHALL 拒绝该上下文且 SHALL NOT 把业务正文写入租约记录

#### Scenario: 续租现有租约
- **WHEN** 有效租约续期成功
- **THEN** `expires_at` 和 Redis TTL SHALL 更新，`acquired_at` 与已有 `work_context` SHALL 保持不变

### Requirement: 支持租约获取后的幂等上下文绑定
Control Service SHALL 提供 `POST /internal/operator-instances/lease/context`，允许先取得租约再领取节点的调度器绑定完整 `work_context`。

#### Scenario: 调度器领取节点后绑定
- **WHEN** 通用调度器取得租约、成功领取节点并提交该租约对应的任务和节点上下文
- **THEN** Control Service SHALL 在租约仍有效时完成绑定，后续查询 SHALL 返回该上下文

#### Scenario: 重复绑定同一上下文
- **WHEN** 调用方因重试对同一有效租约再次提交完全相同的上下文
- **THEN** 绑定 SHALL 幂等成功

#### Scenario: 冲突绑定或绑定过期租约
- **WHEN** 调用方试图把有效租约改绑到不同上下文，或租约已经过期、释放或失效
- **THEN** Control Service SHALL 分别返回冲突或未找到，且 SHALL NOT 创建孤立上下文

### Requirement: 可按实例查询当前活跃工作
Control Service SHALL 提供 `GET /ops/operator-instances/{instance_id}/active-leases`，返回该实例尚未过期的租约、工作上下文、获取/过期时间、活跃租约计数、`reported_inflight` 和可归因差异。

#### Scenario: 查询正在处理或内部等待的任务
- **WHEN** 运维人员查询一个具有已绑定活跃租约的实例
- **THEN** 响应 SHALL 列出对应 `task_id`、`node_id`、`item_id`、`work_type` 和 `trace_id`，并明确这些工作可能正在执行或在算子内部等待

#### Scenario: 查询未绑定租约
- **WHEN** 实例存在先取容量但尚未绑定任务的有效租约
- **THEN** 响应 SHALL 将其标记为未绑定且 SHALL NOT 猜测 `task_id`

#### Scenario: 查询前清理过期记录
- **WHEN** 实例租约有序集合中包含已过期或 Redis 运行标识不匹配的成员
- **THEN** 查询 SHALL 清理这些成员且 SHALL NOT 将其计入活跃数量

### Requirement: 普通算子调用按一次真实调用占用一个租约
平台调用方 SHALL 按真实算子调用粒度申请、必要时续期并在结果处理边界后释放租约，且 SHALL NOT 因算子内部子步骤额外计租约。

#### Scenario: 在线 ASR 会话
- **WHEN** 一个在线 ASR WebSocket 会话建立并持续传输音频
- **THEN** 会话 SHALL 从选择实例到连接关闭持续持有一个 `asr_online` 租约，超过单次 TTL 时 SHALL 续租

#### Scenario: 离线 ASR 与课程脑图
- **WHEN** 离线 ASR 请求或 `/v1/course_overviews` 请求执行
- **THEN** 每次 HTTP 调用 SHALL 各自持有一个租约，且课程脑图内部的大模型多路请求 SHALL NOT 生成额外租约

#### Scenario: PPT Slice 异步受理
- **WHEN** PPT Slice 返回已受理但后台任务尚未终态持久化
- **THEN** 对应 `ppt_slice` 租约 SHALL 持续续期，并 SHALL 在终态结果持久化后释放

#### Scenario: VBas 图片批次
- **WHEN** Vision Orchestrator 向学生或教师接口发送一个包含多帧的批次
- **THEN** 整个 HTTP 批次 SHALL 持有一个共享 VBas 租约且 SHALL 附带任务、批次和流类型上下文

### Requirement: HTTP 请求超时与租约存活相互独立
所有持有容量租约的同步 HTTP 算子调用 SHALL 使用有限硬超时；租约 SHALL 使用独立的短 TTL，调用在单次 TTL 内尚未结束时 SHALL 周期续租。调用完成或失败后 SHALL 立即释放租约；调用方失联后 SHALL 停止续租并由 TTL 自动回收。

#### Scenario: HTTP 调用跨越单次租约 TTL
- **WHEN** 离线 ASR、FaceRec、ScreenDet、OCR、Text Analysis 或 VBas 的同步 HTTP 调用仍未完成且租约接近过期
- **THEN** 对应调用方 SHALL 在保持有限 HTTP 硬超时的同时续租同一个租约，且 SHALL NOT 申请第二个租约替代

#### Scenario: HTTP 调用正常或异常结束
- **WHEN** 算子返回结果、返回错误、请求超时或调用被取消
- **THEN** 调用方 SHALL 停止续租并立即释放租约，释放操作 SHALL 允许幂等重试

#### Scenario: HTTP 租约续租失败
- **WHEN** 同步 HTTP 调用尚未完成但租约续租失败
- **THEN** 调用方 SHALL 不再派生新的算子工作，并 SHALL 按在线错误或离线恢复语义结束本次调用

#### Scenario: 调用方进程失联
- **WHEN** 持有租约的调用方崩溃或无法继续执行续租
- **THEN** 租约 SHALL 在 TTL 到期后自动失效并重新释放实例容量

### Requirement: PPT OCR 和关键词按单图片工作项租赁
`PPT_OCR` 和 `PPT_KEYWORDS` 协调节点 SHALL NOT 各自占用覆盖整节点的算子租约；每个 `ppt_image_id` SHALL 分别申请并释放一个 `ocr` 或 `extract_keywords` 租约，并使用租约返回的实例 URL 发起对应调用。

#### Scenario: 多张 PPT 图片并发 OCR
- **WHEN** 一个 OCR 节点包含多张图片且本地工作项并发大于一
- **THEN** 每张正在调用的图片 SHALL 有自己的 `ocr` 租约，不同图片 SHALL 可被分发到不同 OCR 实例，且 SHALL 不存在额外的节点级 OCR 租约

#### Scenario: 每张 OCR 结果提取关键词
- **WHEN** 多张图片的 OCR 结果先后就绪
- **THEN** 每张图片 SHALL 独立申请 `extract_keywords` 租约、持久化对应关键词结果后释放，且 SHALL NOT 复用覆盖全部图片的 Text Analysis 租约

#### Scenario: 单个图片工作项暂时无容量
- **WHEN** 某个图片工作项申请不到容量而其他工作项已经完成
- **THEN** 已有单项结果 SHALL 保留，未执行项 SHALL 等待后续调度，节点和课程任务 SHALL NOT 因单纯容量不足被标记失败

### Requirement: 在线与离线采用各自的容量不足语义
在线和离线调用 SHALL 使用同一实例池和同一原子准入规则，但调用方 SHALL 按各自既有交互模型处理容量不足。

#### Scenario: 在线请求无容量
- **WHEN** Online Gateway 申请不到目标能力租约
- **THEN** 它 SHALL 不排队、不创建离线节点、不发布 Kafka，并 SHALL 向上游返回 HTTP `200`、业务码 `50301`

#### Scenario: 离线工作无容量
- **WHEN** Orchestrator 的节点或工作项申请不到目标能力租约
- **THEN** 工作 SHALL 保持容量等待并由后续调度重试，Control Service 的内部 HTTP `503` SHALL NOT 成为 A 服务的课程终态响应

#### Scenario: Vision Orchestrator 先于 VBas 启动
- **WHEN** Vision Orchestrator 已取得一条视觉命令，但 VBas 尚未注册或暂无可用容量
- **THEN** Consumer SHALL 保持存活、不提交当前 Kafka offset 并原地等待重试，`/ready` SHALL NOT 仅因容量等待而失败

#### Scenario: 容量等待期间关闭 Vision Orchestrator
- **WHEN** Vision Orchestrator 在等待 VBas 容量时收到关闭信号
- **THEN** Consumer SHALL 终止当前等待且 SHALL NOT 提交未完成命令的 Kafka offset

### Requirement: 活跃租约明细只保存在 Redis
系统 SHALL 使用 Redis 保存高频活跃租约和工作上下文，SHALL NOT 为每次快速申请、绑定、续期和释放新增 PostgreSQL 明细写入；现有任务事实和低频审计边界 SHALL 保持不变。

#### Scenario: 高频 OCR 和关键词请求
- **WHEN** 多节课程产生大量短周期 OCR 与关键词租约
- **THEN** 活跃详情 SHALL 可从 Redis 查询，PostgreSQL SHALL NOT 因每个租约生命周期产生对应高频记录

### Requirement: 视觉抽帧进程并发必须有界
Vision Orchestrator SHALL 使用一个服务进程级可配置正整数上限约束 ffmpeg/ffprobe 子进程并发；教师和学生任务 SHALL 共享该上限，且该本地资源保护 SHALL NOT 改变扫描时间点、VBas 批次租约或平台注册容量。

#### Scenario: 长视频产生大量粗扫时间点
- **WHEN** 一条 T 或 S 长视频一次生成的待抽帧时间点数量大于 `media.max_concurrent_processes`
- **THEN** 同一 Vision Orchestrator 容器内同时运行的抽帧/探测进程 SHALL 不超过该配置，其余时间点 SHALL 等待本地槽位且 SHALL NOT 被丢弃

#### Scenario: 教师与学生任务同时抽帧
- **WHEN** 同一服务进程同时执行教师和学生视频抽帧
- **THEN** 两类任务 SHALL 竞争同一个本地进程上限，且 SHALL NOT 各自获得一份独立上限

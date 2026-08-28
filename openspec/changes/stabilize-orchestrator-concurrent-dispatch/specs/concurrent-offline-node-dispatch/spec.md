## ADDED Requirements

### Requirement: 单一能力使用全部配置化节点槽位
Orchestrator SHALL 在只有一个可调度 capability 时允许该能力使用最多 `worker.node_concurrency` 个节点槽位，同时 MUST 保持算子声明容量为租约硬上限；系统不得退回每种能力每轮只领取一个节点的串行行为。

#### Scenario: ASR 单能力积压
- **WHEN** 100 个 `ASR_TRANSCRIPTION` 节点等待 `asr_offline`，节点并发为 16，三个实例总声明容量为 12
- **THEN** Orchestrator SHALL 最多形成 12 个持有有效租约的并行节点，并在任一槽位释放后继续领取后续节点，直至队列收敛

#### Scenario: 算子容量小于节点槽位
- **WHEN** 可用实例总容量小于 `worker.node_concurrency`
- **THEN** 系统 MUST 保证有效租约不超过声明容量，未取得租约的节点保持可恢复等待状态且不得被写成业务失败

### Requirement: 同一能力每轮只执行一次状态协调
Orchestrator MUST 按唯一 capability 规划并发槽位，禁止同一轮的每个槽位分别批量恢复、延后或聚合该能力的全部节点和任务类型。

#### Scenario: 十六槽位调度同一能力
- **WHEN** 16 个槽位同时调度 `asr_offline`、`ppt_slice` 或 `ocr`
- **THEN** 同一能力的等待状态批量协调 SHALL 最多执行一次，且每个槽位只领取自己的单个节点或工作项

#### Scenario: 多能力混合积压
- **WHEN** `asr_offline`、`ppt_slice` 和 `ocr` 同时存在等待节点
- **THEN** Orchestrator SHALL 按轮转槽位持续服务全部非空能力，列表前部能力不得永久独占节点槽位

### Requirement: 节点从就绪或等待容量状态原子领取
Repository SHALL 使用稳定优先级排序和 `FOR UPDATE SKIP LOCKED` 在一个事务内从状态 10 或 30 选择并领取单个节点；容量恢复不得依赖先把该能力的全部状态 30 节点批量改写为状态 10。

#### Scenario: 容量恢复后领取等待节点
- **WHEN** capability 新取得一个容量租约且同时存在状态 30 的匹配节点
- **THEN** Repository SHALL 原子选择一个节点并将其写为状态 40，其他等待节点保持不变

#### Scenario: 并发领取不同节点
- **WHEN** 多个槽位并发领取同一 capability
- **THEN** 每个成功槽位 MUST 获得不同节点，不得重复领取、覆盖 claim token 或产生同节点双执行

#### Scenario: 取得租约但没有节点
- **WHEN** 槽位取得容量租约后已无匹配的状态 10/30 节点
- **THEN** Orchestrator MUST 幂等释放该租约且不得创建空任务或改变其他节点状态

### Requirement: 能力等待协调不得与任务聚合形成锁环
系统 SHALL 在能力等待批量事务提交后再聚合受影响的任务类型，并 MUST 按稳定顺序处理明确返回的任务 ID；不得在持有批量节点更新锁时扫描并锁定该能力的全部任务类型。

#### Scenario: 容量满载与节点完成重叠
- **WHEN** 一组槽位正在把剩余节点写为状态 30，另一组已运行节点同时完成并聚合任务类型
- **THEN** PostgreSQL 日志 MUST 不出现由调度 SQL 引发的 `40P01`，两类事务均在有界时间内完成或按瞬时错误策略重试

### Requirement: 单槽位瞬时异常不取消其他槽位
一个领取槽位的可恢复数据库或控制面错误 MUST 只影响该槽位；同轮已经取得不同节点的其他槽位 SHALL 继续执行并分别收敛租约和节点状态。

#### Scenario: 一个槽位发生瞬时数据库错误
- **WHEN** 16 个槽位中一个槽位在领取阶段收到可重试 PostgreSQL 错误
- **THEN** 其余成功领取的槽位 SHALL 继续执行，失败槽位释放已有租约并在后续轮次重试

### Requirement: PPT OCR 临时无容量不构成课程终态失败
`PPT_OCR` SHALL 复用已经完成的 `ppt_image_id` 结果，并在 OCR 容量暂不可用时保留未完成工作项用于有界重排；临时容量不足不得把整个 PPT 任务写为状态 70。

#### Scenario: 部分图片完成后 OCR 容量耗尽
- **WHEN** 一个 PPT OCR 节点已有部分单图完成，后续单图暂时无法取得 `ocr` 租约
- **THEN** 已完成结果 MUST 保留，未完成图片 SHALL 等待后续容量，节点不得重复识别已完成图片或立即进入最终失败

### Requirement: 并发调度不改变北向和算子合同
本能力 MUST 保持 A 服务任务提交/查询字段、四种任务类型、整数状态、任务优先级以及七算子 HTTP/WebSocket 合同不变。

#### Scenario: 契约回归
- **WHEN** 修复前后执行北向和算子契约测试
- **THEN** 路径、方法、字段、默认值和响应结构 MUST 保持兼容，差异仅限内部领取、重试、恢复和诊断信息

### Requirement: 修复发布保持现行并发配置
本变更在 `192.168.29.11` 重新 build/run 四平台容器时 MUST 保持部署前记录的现行平台和七算子并发参数；系统不得通过降低 `worker.node_concurrency`、算子声明容量、VBas 批次并发或网关连接上限规避死锁和稳定性验证。

#### Scenario: 重建前后配置对比
- **WHEN** 新平台镜像构建完成并重新创建容器
- **THEN** 宿主机 TOML 摘要、Compose 展开值、容器配置挂载和容器内实际解析值 SHALL 与设计文档冻结基线一致，仅允许出现本变更新增的重试/恢复字段

#### Scenario: 构建缓存复用
- **WHEN** 在 `192.168.29.11` 构建本变更平台镜像
- **THEN** 构建 SHALL 使用现有 BuildKit 缓存，MUST NOT 使用 `--no-cache` 或执行 builder/buildx cache prune，并 SHALL 在报告中记录构建前后缓存与磁盘摘要

### Requirement: 验证通过后精确清理被替代镜像和容器
涉及镜像或容器替换时，系统 SHALL 在新版本通过全部发布门禁后按替换前账本中的完整容器 ID 和镜像 ID 删除被替代版本，同时 MUST 保留构建缓存和不属于本次替换范围的运行资产。

#### Scenario: 新版本验证通过
- **WHEN** 新容器已通过健康、配置、注册、租约和真实业务门禁，且旧/新容器镜像账本完整可验证
- **THEN** 部署流程 SHALL 删除本次被替代的旧容器和旧镜像，并在清理后重验当前容器、当前镜像、BuildKit 缓存和业务数据完整性

#### Scenario: 新版本验证失败
- **WHEN** 新容器任一发布门禁失败
- **THEN** 部署流程 MUST 保留旧回滚镜像，停止新负载并按旧完整 ID 回滚，不得执行旧镜像或构建缓存清理

#### Scenario: 防止宽泛误删
- **WHEN** 执行替代版本清理
- **THEN** 流程 MUST NOT 使用宽泛 container/image/system/builder prune，且不得删除当前镜像、运行容器依赖镜像、基础镜像、未变更算子镜像、volume、模型、Git、`/data/result` 或历史证据

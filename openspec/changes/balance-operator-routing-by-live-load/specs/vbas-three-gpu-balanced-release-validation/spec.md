## ADDED Requirements

### Requirement: 新发布必须在三卡服务器使用缓存构建
本变更 SHALL 在 `192.168.29.11` 以新的完整 Git SHA 构建并发布七算子和四平台同 revision 镜像。构建 MUST 复用现有基础镜像和 BuildKit/镜像层缓存，不得为本变更执行无缓存全量构建或宽泛清理缓存。

#### Scenario: 缓存构建形成同一 revision
- **WHEN** 实现、测试、OpenSpec 和 Harness 已形成并推送新完整 Git SHA
- **THEN** 服务器 MUST 使用现有缓存构建 11 个镜像并逐个 inspect revision，任一镜像 SHA 不一致时禁止替换当前发布

#### Scenario: 构建失败保留当前运行版本
- **WHEN** 任一新镜像构建、模型准备或 revision inspect 失败
- **THEN** 当前运行容器和旧镜像 MUST 保持可用，不得提前删除旧版本或构建缓存

### Requirement: 三卡 VBas 配置和拓扑必须一致
`vbas-gpu0`、`vbas-gpu1`、`vbas-gpu2` MUST 分别绑定物理 GPU 0、1、2，注册 `student_behavior` 和 `teacher_behavior`，且三实例 MUST 同时报告 `declared_capacity=1024`。VBas 配置 MUST 使用 `MaxConcurrentBatches=1024`、`MaxQueueSize=0`；Vision 配置 MUST 使用 `max_batch_size=8`、`max_concurrency=16`。

#### Scenario: 发布预检发现容量漂移
- **WHEN** 任一 VBas 配置、运行时状态、Control 注册或部署拓扑中的容量不等于 `1024`
- **THEN** 发布预检 MUST 失败关闭并列出实例和实际值，不得进入 20 任务验证

#### Scenario: 三实例分别使用三张物理卡
- **WHEN** 三个 VBas 容器完成真实推理
- **THEN** 容器设备绑定、宿主 PID 映射和 `nvidia-smi` MUST 证明三个实例分别在 GPU 0、1、2 上运行

### Requirement: 必须通过二十个真实学生行为任务验证均衡路由
验证程序 SHALL 并发向 `POST /api/course-jobs` 提交 20 个不同 `task_id` 的 `STUDENT_BEHAVIOR` 请求，并 MUST 使用设计文档冻结的 S 视频 URL、前后排坐标、`student_count=70` 和其余原始字段。验证不得直连 VBas 创建业务通过证据。

#### Scenario: 二十个任务全部通过北向接口受理
- **WHEN** 负载程序同时提交 20 个唯一任务请求
- **THEN** 20 个响应 MUST 均可解释为新建或幂等受理，PostgreSQL/Kafka 中不得出现重复逻辑任务，所有任务 ID 必须可通过课程查询接口查询

#### Scenario: 首批并发租约覆盖三个实例
- **WHEN** 三个等容量 VBas 实例健康空闲且至少三个 VBas 批次并发申请租约
- **THEN** 首次三个原子租约 MUST 分别归属 `vbas-gpu0`、`vbas-gpu1` 和 `vbas-gpu2`

#### Scenario: 整个验证窗口不存在固定实例独占
- **WHEN** 20 个课程持续产生批次且三个实例保持健康可用
- **THEN** Control 租约时序和三个 VBas 容器日志 MUST 证明每个实例都接收至少一个真实批次，其他实例持续空闲时不得由排序第一实例长期独占

#### Scenario: 路由证据不能只依赖 nvidia-smi
- **WHEN** `nvidia-smi` 显示三张卡都存在 VBas 进程或利用率
- **THEN** 验证仍 MUST 提供按 `task_id/batch_id/instance_id` 关联的租约与容器日志；仅有 GPU 进程不得判定路由通过

#### Scenario: 任务结果和租约最终收敛
- **WHEN** 停止提交新任务并等待已接受的 20 个任务达到预期终态
- **THEN** 查询结果 MUST 保持现有 `STUDENT_BEHAVIOR` 结构，相关活跃租约、Vision 在途批次和 Kafka lag MUST 最终释放或排空，失败项必须保留中文原因

### Requirement: A 服务调用合同必须保持不变
本变更 MUST 不修改 A 服务的课程提交和查询 HTTP 路径、方法、字段名、字段可选性、响应结构、整数状态和异步处理语义。A 服务 SHALL 不需要增加实例 ID、批次路由、租约或 GPU 字段。

#### Scenario: 既有请求体无需适配
- **WHEN** A 服务继续发送 `task_id`、`task_types`、`priority`、三个视频路径、`front_points`、`back_point`、`student_count` 和 `asr_options`
- **THEN** Control Service MUST 按原合同受理或返回既有业务错误，内部负载路由变化不得出现在必填请求字段中

#### Scenario: 查询结果保持兼容
- **WHEN** A 服务按原接口查询 20 个任务
- **THEN** 任务字典、节点状态、`path/count/result` 和中文 `reason` MUST 保持原有结构，实例分配细节只进入运维与 Harness 证据

### Requirement: Online Gateway 必须通过千路单图并发验证 VBas 三实例路由
系统 SHALL 在离线 20 任务验证之外独立执行既有 `IMG-VBAS-1000` 用例，同时向 `POST /api/online/vbas/analyze` 发起 1000 个合法单图学生行为请求。每个请求 MUST 使用唯一 `ImageId` 和链路标识，图片 MUST 为不超过 5 MiB 的真实可解码图片；请求 MUST 经过 Online Gateway 和 Control 容量租约，不得直连 VBas。

#### Scenario: 千路并发全部完成租约和推理
- **WHEN** 三个等容量 VBas 实例健康空闲，Online Gateway 以 1000 并发发起合法单图请求
- **THEN** 1000 个请求 MUST 全部取得租约并得到既有业务成功响应，不得出现 `50301`、`50000`、网关连接池错误或未解释超时

#### Scenario: 千路在线请求覆盖三个实例
- **WHEN** 1000 个请求持续并发执行且三个 VBas 实例保持健康可用
- **THEN** 首次三个原子租约 MUST 覆盖三个实例，三个实例 MUST 均处理真实请求，其他实例持续空闲时不得由固定实例长期独占

#### Scenario: 千路在线请求证据完整关联
- **WHEN** 在线并发执行完成并生成验证报告
- **THEN** 报告 MUST 包含 Online Gateway 按实例调用增量、Control 租约申请/取得/释放时序、三个 VBas 容器按唯一 `ImageId` 汇总的请求日志和 1000 个响应分类，且不得只用 `nvidia-smi` 判定路由通过

#### Scenario: 在线请求和租约最终收敛
- **WHEN** 1000 个请求全部返回且停止发送新请求
- **THEN** 已取得租约数、实例调用数和成功响应数 MUST 可核对，活跃租约与三个 VBas 实例的 `running_batches` MUST 最终归零，不得遗留隐藏队列

#### Scenario: 负载机没有形成千路并发
- **WHEN** 负载机因文件句柄、可用端口、连接池或网络限制未形成 1000 个同时在途请求
- **THEN** 本次执行 MUST 标记为测试环境无效并记录中文原因，不得据此判定平台通过或把负载机错误归因于 Online Gateway/VBas

### Requirement: 二十离线任务与千路在线请求必须形成真实重叠负载
系统 SHALL 执行专用 `MIXED-VBAS-OFF20-ONLINE1000` 用例。执行器 MUST 先提交 20 个不同 `task_id` 的真实 `STUDENT_BEHAVIOR` 任务，并在观测到 Vision Orchestrator 已持有真实 VBas 活跃租约且三个实例均出现离线批次后，才同时释放 1000 个合法单图 Online Gateway 请求。仅同时完成 HTTP 提交但没有 VBas 执行重叠，不得判定混合场景成立。

#### Scenario: 离线租约形成后释放在线突发
- **WHEN** 20 个课程已受理但尚未观测到 Vision Orchestrator 的真实 VBas 活跃租约
- **THEN** 执行器 MUST 等待并持续采集进度，不得提前释放 1000 路在线请求；超过限定时间仍未形成重叠门槛时场景 MUST 失败关闭

#### Scenario: 在线与离线共享同一实例容量池
- **WHEN** Vision Orchestrator 批次和 Online Gateway 请求在同一窗口申请 VBas 租约
- **THEN** Control Service MUST 使用同一实例活跃租约集合、实时负载和 `declared_capacity` 进行原子选择，不得按调用方、在线/离线或学生/教师能力重复计算容量

#### Scenario: 混合负载覆盖三实例且调用方不持续饥饿
- **WHEN** 1000 路在线请求与 20 个课程的离线批次真实重叠且三个实例保持健康
- **THEN** 三个 VBas 实例 MUST 均处理真实工作，在线请求不得固定命中单一实例，离线批次 MUST 在在线突发期间或突发结束后的有界时间继续取得租约和推进

#### Scenario: 混合负载全部成功并最终收敛
- **WHEN** 三实例健康、声明总容量充足且混合负载执行完成
- **THEN** 1000 个在线请求 MUST 全部返回既有业务成功响应，20 个离线任务 MUST 保持合法状态并最终达到预期终态，活跃租约、VBas `running_batches`、Vision 在途批次和 Kafka 积压 MUST 最终收敛

#### Scenario: 混合证据必须区分调用来源
- **WHEN** 混合验证报告生成
- **THEN** 报告 MUST 按 `source_service`、`work_type`、`task_id`、`batch_id`、在线链路标识和 `instance_id` 区分在线与离线流量，并给出峰值租约、取得/拒绝/释放、响应分类、课程进度、实例请求和 GPU 时序证据

### Requirement: 旧容器和旧镜像只能在新版本验证后精确删除
服务器 SHALL 在替换前记录旧容器完整 ID、镜像完整 ID/digest、Compose 身份和 revision。新版本健康、注册、Smoke、Stage45、20 任务离线均衡验证、1000 路在线 VBas 并发验证和两类负载重叠验证全部通过后，系统 MUST 按完整 ID 删除被替代的旧容器和旧镜像；基础镜像、BuildKit 缓存、volume、模型、Git、`/data/result` 和历史报告 MUST 保留。

#### Scenario: 验证失败可以回滚
- **WHEN** 新容器替换后任一必需门禁失败
- **THEN** 系统 MUST 停止新负载、保留失败证据并使用尚未删除的旧镜像精确恢复旧发布，不得执行旧版本清理

#### Scenario: 验证通过后精确清理
- **WHEN** 新版本全部必需门禁通过且旧版本账本与当前 Docker inspect 一致
- **THEN** 系统 MUST 只删除账本中被替代的旧容器和旧镜像完整 ID，并记录清理前后空间和保护集

#### Scenario: 清理后当前发布仍完整
- **WHEN** 旧版本精确清理完成
- **THEN** 系统 MUST 重新验证当前容器、三卡 VBas、21 个注册实例、7/7 Smoke、共享目录和历史报告，任一漂移都不得发布清理完成结论

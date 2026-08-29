## ADDED Requirements

### Requirement: VBas 必须分别限制在线和离线准入

VBas MUST 从配置读取以下三个参数，并按单实例生效：

- `MaxConcurrentOfflineBatches`：离线同时执行的 batch 数；
- `MaxConcurrentOnlineRequests`：在线同时执行的 HTTP 请求数；
- `MaxQueueOnlineSize`：在线等待队列的最大请求数。

#### Scenario: 在线和离线容量独立
- **WHEN** 一个实例配置 `MaxConcurrentOfflineBatches=1`、`MaxConcurrentOnlineRequests=24`、`MaxQueueOnlineSize=24`
- **THEN** 在线请求不得占用离线运行槽位，离线 batch 不得占用在线运行槽位

#### Scenario: 三实例在线容量
- **WHEN** 三个实例都配置 `MaxConcurrentOnlineRequests=24`
- **THEN** 路由器必须按照三个实例的实时在线负载分配请求，而不是固定选择第一个实例

### Requirement: 在线队列必须有界且按请求计数

VBas MUST 将一个在线 HTTP 请求计为一个在线请求槽位；在线运行数达到 `MaxConcurrentOnlineRequests` 后，最多允许 `MaxQueueOnlineSize` 个请求等待。离线 batch 不得进入该在线队列。

#### Scenario: 在线请求进入队列
- **WHEN** 在线运行数达到 24 且队列未达到 24
- **THEN** 新在线请求必须进入等待队列并保持原始响应语义，不能立即返回容量错误

#### Scenario: 在线队列已满
- **WHEN** 在线运行数达到 24 且等待队列也达到 24
- **THEN** 该实例不得再接纳在线请求，平台必须等待其他实例释放容量或由调用方继续等待

### Requirement: 旧通用队列参数必须移除

系统 MUST 不再读取或依赖 `MaxConcurrentBatches`、`MaxQueueSize` 和 `MaxQueueOfflineSize`。

#### Scenario: 配置文件使用新字段
- **WHEN** VBas 使用新配置启动
- **THEN** 启动日志、注册信息和运行状态必须展示在线运行数、在线队列数和离线运行数

#### Scenario: 旧字段不存在
- **WHEN** 配置文件不包含旧通用队列字段
- **THEN** VBas 必须能够正常启动，不得因为缺少 `MaxQueueSize` 失败

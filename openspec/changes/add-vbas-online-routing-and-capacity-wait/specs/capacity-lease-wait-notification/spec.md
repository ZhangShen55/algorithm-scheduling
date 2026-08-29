## ADDED Requirements

### Requirement: 在线和离线租约申请必须支持有限等待

`online-gateway-service` 和 `vision-orchestrator-service` 在租约暂不可用时 MUST 等待并重新申请，而不是首次申请失败就立即返回容量错误。单次等待上限 MUST 为 300 秒。

#### Scenario: 租约释放后继续处理
- **WHEN** 请求首次申请租约时没有可用实例，且 300 秒内有实例释放对应容量
- **THEN** 调用方必须重新申请并继续执行，不得把首次容量不足直接返回给上游

#### Scenario: 超过等待上限
- **WHEN** 请求连续等待 300 秒仍没有可用容量
- **THEN** 调用方必须结束等待并返回明确的等待超时结果，同时释放本请求已经持有的资源

### Requirement: 租约释放必须发布容量释放通知

Control Service 释放租约时 MUST 在原子删除租约和更新活动租约集合后发布包含能力、容量池和实例标识的 Redis 容量释放通知。

#### Scenario: 释放在线租约
- **WHEN** 一个在线租约成功释放
- **THEN** 等待该在线能力的请求必须能够收到对应实例和容量池的释放通知并立即尝试申请

#### Scenario: 通知丢失
- **WHEN** 等待方没有收到 Redis 通知或 Redis 在等待期间重启
- **THEN** 等待方必须按配置的重试间隔继续轮询，不得永久等待

### Requirement: 租约重试间隔必须可配置

调用方 MUST 支持 `acquire_retry_interval_seconds`，默认值为 `0.2` 秒。该值表示租约申请失败后的基础等待间隔；实现可以在连续失败时退避，但总等待时间不得超过 300 秒。

#### Scenario: 使用默认重试间隔
- **WHEN** 未配置 `acquire_retry_interval_seconds`
- **THEN** 调用方必须使用 0.2 秒作为第一次失败后的基础等待间隔

#### Scenario: 高并发等待
- **WHEN** 大量请求同时等待同一能力的租约
- **THEN** 调用方必须使用退避或随机抖动降低固定频率轮询造成的控制面压力

## ADDED Requirements

### Requirement: 算子主动注册
每个可路由的算子端点 SHALL 使用 `instance_id`、`operator_code`、能力列表、服务 URL、模型/API 版本、GPU/CPU 标签和声明容量向 `control-service` 注册。算子实例 SHALL 向平台注册，而不是向单个适配器注册。

#### Scenario: 注册 VBas 实例
- **WHEN** VBas 容器成功启动
- **THEN** 它注册 `operator_code=vbas`、支持的教师/学生能力、端点、版本和容量

### Requirement: 心跳与生命周期状态
已注册实例 SHALL 定期发送心跳，并提供 `ONLINE`、`DRAINING` 和 `OFFLINE` 生命周期状态。过期实例 SHALL 从路由候选中排除。

#### Scenario: 心跳过期
- **WHEN** 实例未在 TTL 到期前发送心跳
- **THEN** 平台拒绝为其创建新租约，并将该实例显示为离线

#### Scenario: 排空实例
- **WHEN** 运维人员将实例设置为 DRAINING
- **THEN** 实例不再接收新任务，但允许已有任务完成

### Requirement: 健康与状态接口
算子 SHALL 提供 `/ops/health`、`/ops/status` 和 `/ops/drain` 语义，足以区分进程存活、模型就绪、当前容量和优雅排空状态。

#### Scenario: 进程存活但模型加载失败
- **WHEN** `/ops/health` 确认进程存活，但 `/ops/status` 报告模型不可用
- **THEN** 平台不向该实例路由推理请求

### Requirement: 原子容量租约
平台 SHALL 在路由请求前使用带 TTL 的 Redis 原子租约，并在任务完成或租约过期后释放租约。没有可用容量的算子 SHALL 不被选择。

#### Scenario: 并发争抢最后一个槽位
- **WHEN** 两个调度器同时尝试预留最后一个槽位
- **THEN** 只有一个租约成功

### Requirement: 部署端点等同于注册实例
一个可独立访问的进程/端口 SHALL 对应一个注册实例。离线和在线 ASR 的每个容器端点 SHALL 使用一个 Uvicorn worker，并可在每张 GPU 上通过不同端口分别部署。

#### Scenario: 两张 GPU 上运行 ASR
- **WHEN** GPU0 和 GPU1 分别在不同端口运行离线和在线 ASR
- **THEN** 四个可独立选择的实例使用对应能力和 GPU 标签完成注册

### Requirement: 平台只使用 VBas 标识
所有新平台契约 SHALL 使用 `vbas`。平台 SHALL NOT 暴露或持久化旧的 `tias` 别名、路由名、服务码、环境名或容器标识。

#### Scenario: 使用旧代码注册
- **WHEN** 实例尝试注册 `operator_code=tias`
- **THEN** 平台以不支持的算子代码为由拒绝注册

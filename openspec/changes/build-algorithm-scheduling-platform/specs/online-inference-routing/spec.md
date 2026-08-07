## ADDED Requirements

### Requirement: 在线 Base64 图片边界
`online-gateway-service` SHALL 提供 `/api/online/vbas/analyze`、`/api/online/face/recognize` 和 `/api/online/image-quality/detect`。这些接口 SHALL 接受上游调用方提供的图片，并且 SHALL NOT 接入 RTSP、拉取视频流或执行抽帧。

#### Scenario: 提交一张在线学生图片
- **WHEN** A 服务向在线 VBas 接口发送一张 Base64 图片
- **THEN** 网关选择一个就绪的 VBas 实例，转发请求并返回同步结果

### Requirement: 请求级路由
网关 SHALL 将每个完整 HTTP 请求路由到且仅路由到一个算子实例。即使一个请求包含多张图片，也 SHALL NOT 将其拆分到多个实例；不同的并发请求可以路由到不同实例。

#### Scenario: 兼容多图片请求
- **WHEN** 调用方在一个已接受的请求中提交多张图片
- **THEN** 完整请求被发送到同一个实例，并保留每个项目的成功和失败结果

### Requirement: 在线容量隔离
在线路由 SHALL 在不经过 Kafka 的情况下申请和释放算子容量租约。没有就绪容量时，SHALL 返回有界的同步业务响应，并且 SHALL NOT 创建离线课程节点。

#### Scenario: 所有人脸实例均繁忙
- **WHEN** 在线人脸识别请求到达时没有可用的人脸算子租约
- **THEN** 网关返回容量不可用结果，不发布 Kafka 任务

### Requirement: 实时 ASR 会话粘性
网关 SHALL 在 WebSocket 会话建立时选择一个 `asr_online` 实例，并保持该绑定直到会话关闭。实时转写 SHALL 用于直播字幕，默认 SHALL 不替代或持久化正式的离线 ASR 结果。

#### Scenario: 实时会话生成字幕
- **WHEN** 直播播放器打开 WebSocket 并传输音频
- **THEN** 会话内的所有帧都转发到同一个在线 ASR 实例，结果直接返回播放器且不进入离线 DAG

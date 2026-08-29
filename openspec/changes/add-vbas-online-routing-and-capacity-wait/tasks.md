## 1. 共享契约与租约模型

- [x] 1.1 为注册、心跳、租约和运行状态增加在线/离线容量池字段
- [x] 1.2 为租约请求增加工作类型和容量池标识，保持离线 batch 与在线请求计数单位不同
- [x] 1.3 修改 Redis 注册、选择、释放 Lua 脚本，按能力、容量池和实时负载原子分配
- [x] 1.4 在租约释放脚本中发布包含实例、能力和容量池的 Redis 容量释放事件
- [x] 1.5 增加通知丢失、竞态和 Redis 重启时的轮询兜底测试

## 2. VBas 准入与配置

- [x] 2.1 将 `MaxConcurrentBatches` 改为 `MaxConcurrentOfflineBatches`
- [x] 2.2 增加 `MaxConcurrentOnlineRequests`，默认值设为 24
- [x] 2.3 增加 `MaxQueueOnlineSize`，默认值设为 24
- [x] 2.4 删除 `MaxQueueSize` 和 `MaxQueueOfflineSize` 的配置读取、校验和状态字段
- [x] 2.5 改造准入控制器，分别维护离线运行数、在线运行数和在线等待数
- [x] 2.6 为在线排队请求实现有界 FIFO 队列，并在执行完成后正确释放请求和租约
- [x] 2.7 为 `/AE/SyncTasks2`、教师行为和学生行为请求标记在线/离线工作类型
- [x] 2.8 更新 VBas 运行状态、心跳和注册容量上报内容

## 3. 在线网关路由

- [x] 3.1 新增 `/online/vbas/teacher` 并透传教师 VBas 请求和响应
- [x] 3.2 新增 `/online/vbas/student` 并透传学生 VBas 请求和响应
- [x] 3.3 新增 `/online/vbas/person-count` 并透传 `/AE/SyncTasks2` 请求和响应
- [x] 3.4 确保人数接口支持 `AnalysisRule.AlgParams.PolygonList` 坐标区域和原始 `TaskResult`
- [x] 3.5 删除 `/api/online/vbas/analyze` 及 `stream_type` 统一分流逻辑
- [x] 3.6 将图片体积校验和日志指标覆盖到三个新路径
- [x] 3.7 增加网关下游 429、超时和响应结构兼容测试

## 4. 在线与离线租约等待

- [x] 4.1 在在线网关和视觉编排器增加 `acquire_wait_timeout_seconds`，默认 300 秒
- [x] 4.2 增加 `acquire_retry_interval_seconds`，默认 0.2 秒，并实现退避和随机抖动
- [x] 4.3 实现租约释放通知发布与容量等待重试协调器
- [x] 4.4 事件通知和轮询同时发生时，确保同一请求不会重复持有或泄漏租约
- [x] 4.5 等待超过 300 秒时返回明确超时结果并清理请求资源
- [x] 4.6 增加在线 512 请求等待、租约释放后补位和离线容量等待测试

## 5. 文档、部署与验证

- [x] 5.1 更新四服务配置模板、VBas 配置示例和中文注释
- [x] 5.2 更新 A 服务对接文档，记录三个新路由及请求/响应样例
- [x] 5.3 更新部署和运行手册，说明在线队列按实例生效
- [ ] 5.4 完成 VBas 单实例和三实例分配验证
- [ ] 5.5 完成教师、学生、人数三类真实图片请求验证
- [ ] 5.6 完成 `MaxConcurrentOnlineRequests=24`、`MaxQueueOnlineSize=24` 下的 512 并发回归
- [ ] 5.7 运行相关项目测试、`compileall`、健康检查并记录 Harness 证据

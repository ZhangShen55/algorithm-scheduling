## 1. 固化故障和容量测试

- [x] 1.1 增加不同扫描轮次相同 batch_index 不得生成相同 batch ID 的失败测试。
- [x] 1.2 增加空消息 `TimeoutError` 不得产生空节点原因、瞬时失败后重试成功和业务错误不重试的测试。
- [x] 1.3 增加三实例各 `offline=1` 时有效并发为 3、排空实例不计容量和容量变化可刷新的测试。

## 2. 实现动态离线容量门控

- [x] 2.1 实现 Control VBas 容量快照客户端，严格筛选 `ONLINE`、模型就绪实例并汇总 `capacity_pools.offline`。
- [x] 2.2 实现所有课程共享的动态容量门控，使等待批次不提前申请租约或调用 VBas。
- [x] 2.3 将运行时组装切换为动态容量并发，移除固定 `[vbas].max_concurrency` 的权威语义并补充配置注释。

## 3. 修复批次身份和瞬时故障

- [x] 3.1 依据流类型、区域和有序帧集合生成稳定摘要批次 ID，消除教师多阶段扫描的编号碰撞。
- [x] 3.2 对可重试 HTTP 传输异常执行配置化有限重试，每次重新申请和释放租约，业务错误不重试。
- [x] 3.3 完善结构化日志与最终错误原因，记录 batch ID、instance_id、attempt 和异常类型且不记录图片内容。

## 4. 验证和证据

- [x] 4.1 运行 Vision compileall、导入和相关测试，确认 A 服务与 VBas 路由和字段契约未改变。
- [x] 4.2 在 Harness 记录 `test_all_0903_11` 故障证据、重复 ID 根因、修复测试和实际验证层级。
- [x] 4.3 运行 `openspec validate stabilize-vision-vbas-batch-dispatch` 并逐项复审实现与中文 OpenSpec 一致性。

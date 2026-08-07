# Health模型状态脱敏设计

## 目标

`GET /health`的`screen_model`和`occlusion_model`对象不再返回`weights`字段，避免暴露模型绝对路径、相对路径或模型文件名。

## 设计

- 模型holder继续维护完整内部状态，模型加载、预热、启动失败诊断和日志行为不变。
- 健康接口在构造公开响应时复制模型状态并移除`weights`，不直接修改holder返回的状态对象。
- `loaded`、`warmed_up`、`device`及现有运行指标继续返回，ready判定逻辑不变。
- screen和occlusion采用相同脱敏规则，避免两种模型响应不一致。

## 测试

- 请求`GET /health`，断言`screen_model`和`occlusion_model`都不存在`weights`键。
- 同时断言模型加载、预热和ready状态仍正常返回。
- 运行完整单元测试，确认启动与健康检查行为没有回归。

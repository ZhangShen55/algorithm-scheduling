## 1. 新增版本入口与兼容基线

- [x] 1.1 将现有 v1.0.1 WebSocket 会话逻辑隔离为可复用的内部执行函数，并新增 `/v1.0.2/seacraft_asr_online` 入口
- [x] 1.2 增加 v1.0.2 路由和响应兼容性测试，确认 v1.0.1 路径未改变
- [x] 1.3 更新 `asr_online` README，说明 v1.0.2 直连地址和本阶段不修改网关的范围

## 2. v1.0.2 句段状态与修订基础

- [x] 2.1 为 v1.0.2 增加语音段 PCM 缓存、稳定段 `key` 和临时结果状态
- [x] 2.2 接入或封装 VAD 句末判定，并保证标点不会单独触发 `finished=true`
- [x] 2.3 在句末使用 `is_final=True` 冲刷在线模型，避免尾部 token 丢失
- [x] 2.4 对完整语音段执行最终识别和标点处理，并用同一 `key` 返回 `finished=true` 覆盖结果

## 3. 本地验证与产物

- [x] 3.1 使用 `conda run -n asr python -m compileall -q app` 和 `from app.main import app` 完成 CPU 基础检查
- [x] 3.2 使用 `conda` 环境启动 v1.0.2，并通过 `test/test_api_ws_client.py` 对 `test/教师1_16k.wav` 的有效片段完成真实测试
- [x] 3.3 将 v1.0.2 测试输出保存到 `asr_online/test`，记录临时结果、最终修订结果和耗时
- [x] 3.4 运行相关测试并确认 v1.0.1 和 `online_gateway_service` 未被修改

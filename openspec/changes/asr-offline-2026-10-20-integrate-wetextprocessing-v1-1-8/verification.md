# 验证记录

## 远端 GPU

- 主机：`192.168.29.12`，代码目录：`/root/workspace/asr_offline`
- Conda：`asr`，GPU：3 张 NVIDIA GeForce RTX 4090
- 依赖：`WeTextProcessing==1.2.0`、`pynini==2.1.7`、`OpenFST==1.8.4`、`importlib_resources==6.5.2`
- 检查：`pip check`、`python -m compileall -q app`、`from app.main import app` 均通过
- 测试：`python -m unittest discover -s tests -q`，66 项通过
- 服务：GPU 配置启动后 `/ops/health` 返回 `{"status":"alive"}`；`/get_status` 返回 `itn.enabled=true`、`itn.available=true`
- 实际调用：使用 `test_wav/chinEng-16k.wav` 请求 `/v1.1.8/seacraft_asr`，返回 182 段，响应顶层字段完整，时间轴和词时间存在；验证结束后停止临时服务

## Mac

- 主机：当前 Mac，Conda：`asr`
- 依赖：`WeTextProcessing==1.2.0`、`pynini==2.1.7`、`OpenFST==1.8.4`、`importlib_resources==6.5.2`
- 检查：`pip check`、`python -m compileall -q app`、`from app.main import app` 均通过
- 测试：`python -m unittest discover -s tests -q`，67 项通过
- 服务：本地 CPU 服务健康检查通过，`/get_status` 返回 `itn.enabled=true`、`itn.available=true`
- 实际调用：使用同一 `test_wav/chinEng-16k.wav` 请求 v1.1.8，返回 182 段；ITN 日志记录分段统计和耗时，不记录完整转写文本；验证结束后停止临时服务

## 规则回归

- 中文：`二十二摄氏度 -> 22°C`、`百分之五十 -> 50%`、`二零二六年 -> 2026年`
- 术语：`一氧化碳`、`二氧化碳`、`一阶导数`、`二维传热`、`三维空间` 保持中文语义
- 英文：`twenty two degrees Celsius -> 22 degrees Celsius`
- 失败回退、空分段、关闭 ITN、未支持语言跳过和速度统计使用原始 ASR 文本均有测试覆盖

## 提交与推送

- 功能提交：`2f6f277`，`feat(asr_offline): 接入 WeTextProcessing 规范化离线转写`
- 推送结果：已推送到 `origin/codex/milestone-2b-three-gpu-deployment`
- 远端验证结果：GPU 服务健康检查、ITN 可用性检查和真实 v1.1.8 音频调用均成功；临时服务已停止

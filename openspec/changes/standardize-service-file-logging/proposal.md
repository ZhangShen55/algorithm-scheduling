## Why

当前七个算法算子和四个平台服务的日志目录、轮转方式、保留周期及敏感内容控制不一致，容器重建后也缺少统一的宿主机持久化约定，难以支撑里程碑 2B 的故障定位与容量管理。需要在七算子新基线落地前建立统一、可配置、可验证的文件日志合同，同时保持现有接口和业务运行行为不变。

## What Changes

- 为 `asr_offline`、`asr_online`、`facerec`、`ocr`、`screen_det`、`ppt_slice`、`vbas` 以及四个平台服务统一增加项目根目录 `logs/` 文件日志。
- 单个日志文件最大为 `100 MiB`，归档日志保留 `7` 天；两项均通过各项目 `config.toml` 配置并提供相同默认值。
- 文件日志与容器 `stdout` 同时输出；多实例通过 `logs/{instance_id}/application.log` 隔离，远端 Compose 将目录持久化到 `/data/logs/algorithm-scheduling/{service}/{instance_id}`。
- 统一结构化上下文字段、日志级别、异常记录和启动配置摘要，并禁止记录 Base64/媒体内容、完整请求体、Token、完整 ASR/OCR 文本及其他敏感大字段。
- 对本次新增或修改的非直观日志初始化、大小轮转、七日清理、脱敏和上下文传播逻辑补充简洁中文代码注释；注释不得引入业务分支、改变接口合同、影响模型推理或改变进程运行方式。
- 为目录创建、大小轮转、七日清理、多实例隔离、配置解析、敏感内容排除、stdout 共存和运行行为不变增加自动化验证及 Harness 证据。
- 明确排除 `text_analysis/`：不修改其源码、配置、日志、镜像或部署；它继续作为非平台项目保留。

## Capabilities

### New Capabilities

- `service-file-logging`: 定义七个当前平台算子和四个平台服务的统一文件日志、轮转保留、实例隔离、敏感内容控制、必要代码注释及验证要求。

### Modified Capabilities

无。

## Impact

- 受影响项目：七个算法算子、`control_service`、`orchestrator_service`、`vision_orchestrator_service`、`online_gateway_service`。
- 受影响配置与部署：各项目 `config.toml`、日志初始化代码、README、Dockerfile/Compose 日志挂载、`.gitignore`、平台部署预检和 Harness。
- 不改变现有 HTTP/WebSocket 路径、请求响应字段、默认端口、任务 DAG、算子注册/租约合同和数据库结构。
- 本变更与 `retire-text-analysis-from-scheduling-platform` 共用七算子目标拓扑；实施顺序为先完成本变更的本地实现与验证，再完成退役变更的本地实现与验证，两个变更必须在同一最终 Git SHA 上完成远端构建与里程碑 2B 验收，避免重复构建。

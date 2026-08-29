## Why

当前离线 ASR 的默认参数为 `showSpk=true`、`showEmotion=true`，与新的业务默认要求不一致。同时，课程任务按 `task_id + task_type` 幂等后，后续使用同一 `task_id` 但改变 ASR 参数时无法判断该参数版本是否已经处理过，也无法在需要时安全地重新处理。

本变更使 ASR 参数成为可追踪的执行版本：新任务默认关闭说话人和情绪识别；相同参数复用已有结果；不同且未成功处理过的参数创建新的执行版本并重新调度。

## What Changes

- 将新建 ASR 任务的默认 `showSpk` 改为 `false`。
- 将新建 ASR 任务的默认 `showEmotion` 改为 `false`。
- 对规范化后的完整 `effective_params` 生成稳定参数指纹。
- 支持同一 `task_id` 的 ASR 存在多个参数执行版本。
- 相同参数已成功处理时直接复用结果，不重复调用 ASR 算子。
- 相同参数正在处理时返回当前执行版本，不重复发布任务。
- 参数不同且没有成功处理记录时创建新执行版本并发布调度事件。
- 查询接口返回本次请求参数对应的 ASR 执行状态、参数和结果。
- 保留历史参数版本和结果，禁止用新参数静默覆盖旧结果。
- 补充重复提交、参数切换、并发提交和默认值的自动化测试。

## Capabilities

### New Capabilities

- `asr-parameter-versioned-execution`: 定义 ASR 参数规范化、参数指纹、执行版本幂等、重新调度和结果查询行为。

### Modified Capabilities

无。当前主规范中没有可直接修改的 ASR 能力规范，本变更新增独立能力规范。

## Impact

- `control_service` 的 ASR 请求参数模型、任务提交和查询逻辑。
- 调度平台 PostgreSQL 迁移，增加 ASR 执行版本及参数指纹的持久化能力。
- `orchestrator_service` 的节点初始化、执行版本关联和结果回写。
- ASR 任务响应结构及相关平台契约测试。
- 不改变 ASR 算子 HTTP 接口路径和请求字段；算子仍接收平台传入的最终参数。

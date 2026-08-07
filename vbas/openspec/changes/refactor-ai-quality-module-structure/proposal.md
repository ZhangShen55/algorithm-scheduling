## Why

`app/ai_quality` 当前把 Worker 入口、任务编排、Kafka、DB、视频处理、图片存储、模型适配、指标聚合和策略规则全部平铺在同一层。第一版功能已经跑通，但继续增加表、指标和策略时，现有结构会让职责边界不清、依赖方向不明显、测试定位成本升高。

本次变更只做模块结构整理，目标是在不改变业务行为、不改变启动方式、不改变数据库口径的前提下，把 AI 课堂视觉分析 Worker 整理成可持续维护的分层结构。

## What Changes

- 将 `app/ai_quality` 从单层平铺结构整理为分层目录：
  - `application/`：任务编排、Worker、依赖组装、应用常量。
  - `domain/`：指标聚合、快照策略、学生异常行为统计、评分、稳定 ID 等纯业务规则。
  - `infrastructure/`：Kafka、MySQL、视频、抓拍存储、视觉模型适配等外部依赖封装。
- 保留 `python -m app.ai_quality.app ...` 作为启动入口，避免运行命令变化。
- 优先通过移动文件和更新 import 完成结构整理，业务逻辑保持等价。
- 将 `repositories.py` 的多表职责拆分或准备拆分为更清晰的 DB 仓储边界，避免后续继续膨胀。
- 更新测试 import 和必要文档，确保新结构可被后续开发直接沿用。
- 重构后必须重新执行全量视频任务，验证完整链路仍可落库成功。
- 不新增功能、不调整指标算法、不调整 `lesson_snapshot_event` 或 `lesson_student_behavior_stat` 数据口径。

## Capabilities

### New Capabilities

- `ai-quality-module-structure`：定义 AI 课堂视觉分析 Worker 的模块分层、职责边界、兼容入口和重构后验证要求。

### Modified Capabilities

- 无。

## Impact

- 影响代码：`app/ai_quality/` 下模块路径、import 路径、测试 import、README 或 OpenSpec 相关说明。
- 影响运行：启动入口和配置文件路径保持不变；Worker 行为、Kafka 消息格式、数据库写入表和字段保持不变。
- 影响测试：需要更新单元测试 import；运行 `tests/test_ai_quality*.py`；再使用本地 Nginx 视频 URL 跑一条全量任务并核对 DB。
- 兼容性：本次不改变对外 HTTP 接口，不改变 Kafka topic/消息字段，不改变数据库表结构，不改变落库相对图片路径。

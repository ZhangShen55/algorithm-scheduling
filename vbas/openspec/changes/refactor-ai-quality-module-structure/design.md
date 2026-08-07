## Context

`app/ai_quality` 是第一版 AI 课堂视觉分析 Worker 的实现目录，当前已经包含 Kafka 消费、任务消息解析、视频下载抽帧、模型分析适配、核心快照策略、学生异常行为统计、指标聚合评分、MySQL 仓储和 CLI 启动入口。

当前目录是单层平铺结构：

```text
app/ai_quality/
  app.py
  worker.py
  config.py
  db.py
  repositories.py
  kafka_consumer.py
  message.py
  video.py
  storage.py
  frame_analyzer.py
  aggregator.py
  snapshot_policy.py
  student_behavior_stat.py
  scoring.py
  ids.py
```

这套结构在第一版快速落地时可接受，但后续继续增加指标、行为统计表、事件策略和部署能力时，会出现几个问题：

- 业务规则和外部基础设施混在同一层，依赖方向不清。
- `worker.py` 同时了解视频、模型、快照、聚合、评分、DB 写入，后续容易继续变重。
- `repositories.py` 包含多个表的 SQL，已经接近 300 行，继续加表会变成维护瓶颈。
- 测试文件需要理解平铺文件名，无法从路径看出模块职责。

本次重构是结构整理，不改变业务口径。特别是以下行为必须保持：

- `python -m app.ai_quality.app --config app/config.toml run-json ...` 启动方式保持可用。
- Kafka 消息字段、重试和 offset 语义保持不变。
- `lesson_ai_job`、`lesson_ai_workflow`、`lesson_behavior_timeline`、`lesson_snapshot_event`、`lesson_student_behavior_stat`、`indicator_score_result` 写入语义保持不变。
- 抓拍 `image_url` 仍只保存 `cv/{task_id}/{image_id}.png` 相对路径。
- 指标算法、快照策略和行为统计策略保持等价。

## Goals / Non-Goals

**Goals:**

- 将 `app/ai_quality` 整理为清晰的分层目录，让后续开发能从路径判断职责。
- 保持现有 Worker 启动方式和业务行为不变。
- 将纯业务规则集中到 `domain/`，将 Kafka、DB、视频、存储、模型适配等外部依赖集中到 `infrastructure/`。
- 将任务编排集中到 `application/`，让 Worker 更像流程编排器，而不是规则和基础设施的混合体。
- 拆分或准备拆分 `repositories.py` 的多表职责，让 DB 写入边界更清楚。
- 更新所有测试 import，并通过单元测试、启动 smoke 验证和全量视频重跑验证。

**Non-Goals:**

- 不新增任何 AI 课堂质量功能。
- 不改变 Kafka 消息格式、topic 或 consumer group 语义。
- 不改变数据库表结构、字段、唯一键或写入数据口径。
- 不改变指标算法、评分规则、核心快照策略、学生异常行为统计策略。
- 不优化模型推理性能，不改变抽帧间隔。
- 不重写现有视觉模型服务。

## Decisions

### 1. 采用三层目录：application / domain / infrastructure

目标结构：

```text
app/ai_quality/
  __init__.py
  app.py
  config.py

  application/
    __init__.py
    worker.py
    factories.py
    constants.py

  domain/
    __init__.py
    ids.py
    metrics.py
    scoring.py
    snapshots.py
    behavior_stats.py

  infrastructure/
    __init__.py
    kafka/
      __init__.py
      consumer.py
      message.py
    db/
      __init__.py
      connection.py
      repositories.py
    media/
      __init__.py
      video.py
      snapshot_storage.py
    vision/
      __init__.py
      frame_analyzer.py
```

职责划分：

- `application/`：处理“一个课次任务如何执行”的流程编排和依赖组装。
- `domain/`：不直接访问 Kafka、MySQL、文件系统和模型服务，只处理数据结构、指标、策略、评分和稳定 ID。
- `infrastructure/`：封装外部系统和重 I/O，包括 Kafka、MySQL、视频下载抽帧、抓拍写盘、现有视觉模型调用。

理由：

- 这是当前代码最自然的边界：业务规则和外部依赖能明确分开。
- 后续新增指标或快照策略时优先进入 `domain/`，新增表写入时进入 `infrastructure/db/`。
- Worker 只依赖抽象后的流程部件，读起来更像业务流水线。

备选方案：

- 按 `models/`、`services/`、`utils/` 分类：路径看似常见，但会把规则和外部依赖继续混在一起，无法解决当前痛点。
- 只给现有文件改名不分目录：改动小，但后续扩展仍然平铺。

### 2. 保留顶层 `app.py` 和 `config.py`

`app.py` 保留在顶层作为 CLI 入口，继续支持当前命令：

```bash
python -m app.ai_quality.app --config app/config.toml run-json ...
python -m app.ai_quality.app --config app/config.toml consume
```

`config.py` 也保留在顶层，作为应用配置的稳定入口。各子包可以依赖 `AiQualityConfig`，避免把配置隐藏到某个基础设施目录造成依赖反向。

理由：

- 减少部署脚本、README 和手动联调命令变化。
- 配置对象是跨层依赖，放顶层更清晰。

备选方案：

- 将 `app.py` 移到 `application/cli.py`：结构更纯，但启动命令变化会引入额外风险。
- 将 `config.py` 放入 `infrastructure/`：不准确，因为配置同时服务应用层和基础设施层。

### 3. 第一轮以“移动文件 + 更新 import”为主，避免行为重写

迁移第一阶段只做等价移动：

| 当前文件 | 目标文件 |
|---|---|
| `worker.py` | `application/worker.py` |
| `aggregator.py` | `domain/metrics.py` |
| `snapshot_policy.py` | `domain/snapshots.py` |
| `student_behavior_stat.py` | `domain/behavior_stats.py` |
| `scoring.py` | `domain/scoring.py` |
| `ids.py` | `domain/ids.py` |
| `kafka_consumer.py` | `infrastructure/kafka/consumer.py` |
| `message.py` | `infrastructure/kafka/message.py` |
| `db.py` | `infrastructure/db/connection.py` |
| `repositories.py` | `infrastructure/db/repositories.py` |
| `video.py` | `infrastructure/media/video.py` |
| `storage.py` | `infrastructure/media/snapshot_storage.py` |
| `frame_analyzer.py` | `infrastructure/vision/frame_analyzer.py` |

第一轮不重写函数，不改变 SQL，不调整算法。需要更新 import 和测试路径。

理由：

- 结构重构最容易引入 import 和启动链路问题，先保证行为等价。
- 避免“结构重构”和“业务优化”混在一个变更里，降低评审成本。

### 4. Repository 拆分分两步处理

`repositories.py` 是当前最需要后续拆分的文件，但建议不要在第一步移动文件时同时大拆。

推荐策略：

1. 第一轮移动到 `infrastructure/db/repositories.py`，保持 `AiQualityRepository` 门面不变。
2. 如果移动后测试全部稳定，再拆出表级私有模块或类：
   - `workflow_repo.py`
   - `job_repo.py`
   - `timeline_repo.py`
   - `snapshot_repo.py`
   - `behavior_stat_repo.py`
   - `indicator_repo.py`
3. 对外仍保留 `AiQualityRepository` 门面，Worker 不直接依赖多个表级 repo。

理由：

- Worker 当前只需要一个仓储门面，表级拆分不应泄漏到应用编排层。
- 分两步可以让 import 迁移和 SQL 拆分分别验证。

备选方案：

- 一次性拆成多个 repo 并改 Worker 构造函数：边界更纯，但对测试和编排影响更大。

### 5. 兼容模块按需保留，不长期依赖

如果测试或外部脚本直接 import 旧路径，例如 `app.ai_quality.worker`，可以短期保留薄 wrapper：

```python
from app.ai_quality.application.worker import VisualAnalysisWorker
```

但重构完成后，项目内部测试和代码应全部迁移到新路径。旧 wrapper 只作为兼容层，不再新增逻辑。

理由：

- 当前仓库内测试可以一起更新，但外部手工脚本可能仍引用旧路径。
- 保留薄 wrapper 可以降低切换风险，但不能让旧结构继续成为主路径。

## Risks / Trade-offs

- [Risk] 大量 import 路径变化可能造成漏改。  
  Mitigation：使用 `rg "app.ai_quality"` 全量扫描；运行 `tests/test_ai_quality*.py`；运行 CLI smoke。

- [Risk] 启动入口变化会影响本地联调和部署脚本。  
  Mitigation：保留顶层 `app.py`，启动命令不变。

- [Risk] Repository 拆分时可能改变 SQL 参数或事务提交节奏。  
  Mitigation：第一轮保持仓储门面和 SQL 逻辑不变；拆分时只做类/文件边界拆分，不调整 SQL。

- [Risk] 全量视频验证耗时较长。  
  Mitigation：结构重构后必须执行一次全量视频任务；单元测试先跑通后再跑全量，避免浪费长时间验证。

- [Risk] 旧路径 wrapper 可能让新旧结构并存过久。  
  Mitigation：项目内部 import 必须使用新路径；wrapper 只做兼容，不承载逻辑。

## Migration Plan

1. 建立新目录和 `__init__.py`。
2. 按目标结构移动文件，先不改业务逻辑。
3. 更新应用代码 import。
4. 更新测试 import。
5. 运行 `python -m compileall app/ai_quality`。
6. 运行 `python -m pytest tests/test_ai_quality*.py -q`。
7. 运行 CLI smoke，至少验证 `--help` 或最小 JSON 任务链路可启动。
8. 使用本地 Nginx 视频 URL 跑一条全量任务。
9. 查询 DB，确认任务成功、核心表有结果，尤其确认 `lesson_student_behavior_stat`、`lesson_snapshot_event`、`indicator_score_result` 仍正常写入。

回滚策略：

- 本次是代码结构变更，不涉及数据迁移。若出现问题，可回退文件移动和 import 修改。
- 已写入测试任务数据可按同一 `task_id` 重跑覆盖。

## Open Questions

无。当前整理目标、目录结构、兼容入口和全量验证要求已经明确。

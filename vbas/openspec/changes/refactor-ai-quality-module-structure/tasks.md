## 1. 结构准备

- [x] 1.1 创建 `application/`、`domain/`、`infrastructure/` 及 Kafka、DB、media、vision 子目录，并补齐 `__init__.py`。
- [x] 1.2 清理 `app/ai_quality/__pycache__` 等生成文件，避免重构时混淆真实源码文件。
- [x] 1.3 确认顶层 `app.py` 和 `config.py` 保留为稳定入口，不改变 CLI 启动命令。

## 2. 领域规则迁移

- [x] 2.1 将 `aggregator.py` 移动为 `domain/metrics.py`，保持 `StudentFrameMetric`、`TeacherFrameMetric` 和指标聚合行为不变。
- [x] 2.2 将 `snapshot_policy.py` 移动为 `domain/snapshots.py`，保持核心快照筛选行为不变。
- [x] 2.3 将 `student_behavior_stat.py` 移动为 `domain/behavior_stats.py`，保持统计窗口、候选段排序和最多 5 段规则不变。
- [x] 2.4 将 `scoring.py` 和 `ids.py` 移动到 `domain/`，保持评分和稳定 ID 生成不变。

## 3. 基础设施迁移

- [x] 3.1 将 `kafka_consumer.py` 和 `message.py` 移动到 `infrastructure/kafka/`，保持 Kafka 消费、重试、offset 和消息解析语义不变。
- [x] 3.2 将 `db.py` 和 `repositories.py` 移动到 `infrastructure/db/`，保持 MySQL 连接、SQL、事务提交和仓储门面不变。
- [x] 3.3 将 `video.py` 移动到 `infrastructure/media/video.py`，保持下载、时长读取和抽帧行为不变。
- [x] 3.4 将 `storage.py` 移动到 `infrastructure/media/snapshot_storage.py`，保持抓拍缩放、保存路径和相对路径生成不变。
- [x] 3.5 将 `frame_analyzer.py` 移动到 `infrastructure/vision/frame_analyzer.py`，保持现有学生和教师模型适配行为不变。

## 4. 应用编排迁移

- [x] 4.1 将 `worker.py` 移动到 `application/worker.py`，并更新依赖到新的 domain 和 infrastructure 路径。
- [x] 4.2 新增或调整 `application/factories.py`，承载 Worker 依赖组装逻辑；顶层 `app.py` 只保留 CLI 和命令分发。
- [x] 4.3 新增或调整 `application/constants.py`，集中管理 `INDICATOR_CODES` 等应用级常量。
- [x] 4.4 如有必要，保留旧路径薄 wrapper，但项目内部 import 必须迁移到新路径。

## 5. 测试和文档更新

- [x] 5.1 更新 `tests/test_ai_quality*.py` 中所有 `app.ai_quality` import 路径。
- [x] 5.2 更新 README 中 AI Quality Worker 结构说明和本地启动说明。
- [x] 5.3 使用 `rg "app.ai_quality"` 扫描源码和测试，确认不存在错误旧路径或循环依赖。
- [x] 5.4 运行 `python -m compileall app/ai_quality`，确认新结构可编译。

## 6. 自动化验证

- [x] 6.1 运行 `python -m pytest tests/test_ai_quality*.py -q`，确认 AI Quality 测试集通过。
- [x] 6.2 运行 CLI smoke，确认 `python -m app.ai_quality.app --help` 或等价命令可加载。
- [x] 6.3 使用快速 JSON 任务或最小 smoke 验证 Worker 依赖组装可执行。

## 7. 全量视频验证

- [x] 7.1 使用本地 Nginx 课程视频 URL 重新跑一条全量课次任务，配置不得启用 `MaxFramesPerVideo` 限制。
- [x] 7.2 查询数据库确认 `lesson_ai_job` 和 `lesson_ai_workflow` 成功，错误信息为空。
- [x] 7.3 查询数据库确认 `lesson_behavior_timeline`、`lesson_snapshot_event`、`lesson_student_behavior_stat` 和 `indicator_score_result` 均按实际检测结果写入。
- [x] 7.4 核对抓拍文件写入当前配置的项目内 `blobstor/image` 目录，且数据库 `image_url` 仍为 `cv/{task_id}/{image_id}.png` 相对路径。

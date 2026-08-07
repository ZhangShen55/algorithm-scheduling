# Text Analysis 项目重命名设计

## 背景

工作区中的教育场景文本分析服务当前目录名为 `llm_api_refactor`。该名称描述了历史重构过程，没有表达服务的长期业务职责，也与已经完成统一命名的算法算子不一致。

服务当前使用真实 `app/` Python 包并通过 `app.main:app` 启动。现有代码包含课程脑图、关键词提取及其他历史文本分析接口。本次只调整项目身份和工作区边界，不裁剪接口、不重构业务代码。

## 目标

- 将项目目录从 `llm_api_refactor/` 重命名为 `text_analysis/`。
- 将 README 和 FastAPI 展示名称统一为 Text Analysis。
- 将项目加入根级算子地图，并补充项目级 Harness 规则。
- 更新活动 README、Docker、Shell 和工作区 Markdown 中的当前路径引用。
- 删除项目内不再维护的历史 `openspec/` 目录。
- 最终移除 `text_analysis/.git`，使其与另外七个算子一起由未来的工作区根仓库统一管理。

## 非目标

- 不改变任何 HTTP 路径、方法、请求字段、响应字段或默认端口。
- 不移除当前已挂载的历史接口。
- 不把服务限制为只暴露 `/v1/course_overviews` 和 `/v1/extract_keywords`；接口裁剪需要单独设计。
- 不修改 LLM Prompt、模型调用、超时、重试、统计或业务处理逻辑。
- 不初始化工作区根 Git 仓库。

## 目录和标识变更

| 项目 | 变更前 | 变更后 |
| --- | --- | --- |
| 项目目录 | `llm_api_refactor/` | `text_analysis/` |
| Python 包 | `app/` | `app/`（不变） |
| Uvicorn 入口 | `app.main:app` | `app.main:app`（不变） |
| README 标题 | `LLM API (Refactored)` | `Text Analysis API` |
| FastAPI 展示标题 | `LLM API` | `Text Analysis API` |
| 默认端口 | `8000` | `8000`（不变） |

新增 `text_analysis/AGENTS.md`，记录服务职责、入口、配置、主要接口、测试方式和兼容边界。根 `AGENTS.md` 的项目地图从七个算子扩展为八个算子。

## 兼容性边界

目录名称只影响本地文件路径、Docker 构建上下文和人工操作命令。调用方继续使用原有网络地址和接口路径，不需要随目录重命名修改请求。

FastAPI 展示标题只用于 OpenAPI 元数据，不改变路由或响应业务结构。配置中的外部依赖地址、模型参数和接口标签保持原值。

## Git 元数据处理

迁移开始前记录 `llm_api_refactor` 的 Git 状态、remote、目录清单和路由清单。迁移期间保留 `.git` 用于差异核对。

只有在以下门禁全部通过后，才将 `text_analysis/.git` 移出项目目录：

1. 新目录、`app/main.py`、README 和 `AGENTS.md` 存在。
2. 旧目录不存在。
3. `app.main:app` 可以导入。
4. 迁移前后业务路由及 HTTP 方法一致。
5. 编译、现有测试和依赖检查没有新增失败。
6. Docker、Shell 和活动文档没有继续使用旧路径。

为降低不可恢复风险，Git 元数据先移动到明确的 `/tmp` 备份目录；确认项目内不存在 `.git` 后记录备份位置。该备份可能被操作系统清理，不能作为长期版本库。

## 历史 OpenSpec 处理

`llm_api_refactor/openspec/` 只包含已经实现或停止维护的历史变更资料，运行时代码、Docker、启动脚本、配置和测试均未引用该目录。迁移时删除整个目录，不移动到 `text_analysis/`，也不在新项目中保留归档副本。

删除范围仅为项目内的 `openspec/`。工作区根目录下的设计文档和其他项目资料不受影响。

## 验证

- `python -m compileall -q app`
- `python -m pytest -q tests`
- `python -m pip check`
- 导入 `from app.main import app`
- 对比迁移前后的 OpenAPI 路径、HTTP 方法及非 OpenAPI 路由
- 检查 Dockerfile 和 `start.sh` 仍使用 `app.main:app`
- 检查根级和项目级 `AGENTS.md`
- 检查 `text_analysis/openspec` 不存在
- 检查 `text_analysis/.git` 不存在且工作区根目录仍未初始化 Git

## 风险控制

- 项目当前包含多个历史接口，本次不做接口清理，避免把目录迁移与行为变更混在一起。
- 当前子仓库如有用户修改或未跟踪文件，迁移必须原样保留。
- 仅删除已确认无运行时引用的项目级历史 `openspec/`；日志或测试结果中的旧路径可以保留。
- 如果测试依赖外部 LLM 服务，离线可重复测试和路由契约检查作为结构迁移门禁；外部服务不可用需要如实记录，不能通过修改业务逻辑规避。

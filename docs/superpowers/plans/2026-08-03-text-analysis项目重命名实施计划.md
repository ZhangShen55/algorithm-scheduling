# Text Analysis 项目重命名 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `llm_api_refactor` 统一重命名为 `text_analysis`，同步项目身份和工作区文档，删除历史 OpenSpec 与子项目 Git 元数据，同时保持全部接口和算法行为不变。

**Architecture:** 先保存子仓库状态、目录和完整路由基线，再原地重命名目录。结构迁移只更新项目身份、Harness 和活动路径引用；测试、入口、路由及 Docker 校验全部通过后，才移除历史 `openspec/` 和项目 `.git`。

**Tech Stack:** Python 3.10、FastAPI、Uvicorn、Conda `ai_report`、Docker、unittest/pytest。

**实施状态（2026-08-03）：** 全部步骤已完成。迁移后 24 项测试通过，20 条 OpenAPI 路径和 24 条完整路由与基线一致。项目 Git 元数据临时备份于 `/tmp/text-analysis-git-backup.BR4rzO/text_analysis.git`；已移除的历史 OpenSpec 临时备份于 `/tmp/text-analysis-openspec-backup.SEWiDj/openspec`。

---

## 文件结构与责任

- `text_analysis/app/main.py`：现有 FastAPI 应用装配；仅将展示标题改为 `Text Analysis API`。
- `text_analysis/README.md`：项目说明和本地启动命令。
- `text_analysis/AGENTS.md`：服务职责、入口、配置、兼容约束与验证命令。
- `AGENTS.md`：工作区八个算法算子的公共规则与项目地图。
- `docs/design/algorithm-scheduling-platform-detailed-design.md`：调度架构中的文本分析项目路径。
- `docs/superpowers/plans/2026-08-03-算子项目统一结构整改实施计划.md`：前一轮迁移结果说明，更新为文本算子已纳入统一工作区。
- `text_analysis/openspec/`：已确认无运行时引用的历史资料，删除。
- `text_analysis/.git/`：只在最终验收后移出项目目录。

根目录不是 Git 仓库，本计划不创建分支、不提交 commit，也不初始化根仓库。

### Task 1: 保存迁移基线

**Files:**
- Create: `/tmp/text-analysis-rename-20260803/status.txt`
- Create: `/tmp/text-analysis-rename-20260803/tree.txt`
- Create: `/tmp/text-analysis-rename-20260803/routes.txt`

- [x] **Step 1: 记录 Git 状态和 remote**

从工作区根目录执行：

```bash
mkdir -p /tmp/text-analysis-rename-20260803
git -C llm_api_refactor status --short
git -C llm_api_refactor remote -v
```

预期：状态为空；remote 为 `git@github.com:ZhangShen55/jy-service-app-ai_analysis.git`。将输出保存到基线目录。

- [x] **Step 2: 记录目录清单和关键文件摘要**

记录 `.git` 之外的文件相对路径，并确认 `app/main.py`、`config.toml`、`prompt/`、`tests/`、Dockerfile、`start.sh` 和 README 存在。迁移过程中不得丢失这些内容；唯一计划删除的业务目录是 `openspec/`。

- [x] **Step 3: 记录完整接口基线**

使用 `ai_report` 环境导入 `app.main:app`，保存 OpenAPI 中的路径及方法，并补充 OpenAPI 之外的路由。预期导入成功，输出写入 `/tmp/text-analysis-rename-20260803/routes.txt`。

### Task 2: 写项目身份回归测试

**Files:**
- Create: `llm_api_refactor/tests/test_project_identity.py`

- [x] **Step 1: 写失败测试**

新增：

```python
import unittest
from pathlib import Path

from app.main import app


class ProjectIdentityTests(unittest.TestCase):
    def test_application_uses_text_analysis_identity(self):
        self.assertEqual(app.title, "Text Analysis API")

    def test_runtime_entrypoint_remains_app_main(self):
        self.assertIn("APP_MODULE=app.main:app", Path("Dockerfile").read_text(encoding="utf-8"))
        self.assertIn('APP_MODULE="${APP_MODULE:-app.main:app}"', Path("start.sh").read_text(encoding="utf-8"))
```

- [x] **Step 2: 运行测试并确认 RED**

```bash
conda run -n ai_report python -m unittest tests.test_project_identity -v
```

预期：应用标题仍为 `LLM API`，第一项断言失败；启动入口断言通过。

### Task 3: 重命名项目并同步身份

**Files:**
- Rename: `llm_api_refactor/` -> `text_analysis/`
- Modify: `text_analysis/app/main.py`
- Modify: `text_analysis/README.md`
- Create: `text_analysis/AGENTS.md`
- Modify: `AGENTS.md`

- [x] **Step 1: 重命名项目目录**

确认 `text_analysis/` 不存在后，将 `llm_api_refactor/` 原样移动为 `text_analysis/`。立即确认旧目录不存在、新目录及 `.git` 均存在。

- [x] **Step 2: 更新应用和 README 标识**

将 `text_analysis/app/main.py` 中：

```python
app = FastAPI(title="LLM API", version="1.0.0")
```

改为：

```python
app = FastAPI(title="Text Analysis API", version="1.0.0")
```

README 一级标题改为 `# Text Analysis API`。启动命令、配置说明、接口内容和默认端口保持原样。

- [x] **Step 3: 创建项目级 Harness 规则**

`text_analysis/AGENTS.md` 必须记录：

- 职责是教育场景文本分析。
- Conda 环境 `ai_report`、默认端口 `8000`、入口 `app.main:app`。
- 根级 `config.toml`、`prompt/` 和 `CONFIG_PATH` 规则。
- `/v1/course_overviews` 与 `/v1/extract_keywords` 是调度平台当前使用接口，但现有其他接口不得在本次迁移中删除。
- HTTP 契约、Prompt 和 LLM 行为不可因结构工作改变。
- 编译、测试、依赖和路由验证命令。

- [x] **Step 4: 更新根级项目地图**

在根 `AGENTS.md` 增加：

```markdown
| `text_analysis` | Course mind-map and PPT keyword text analysis | `ai_report` | `8000` |
```

并将“七个算子”相关表述更新为八个算子，不修改其他项目规则。

- [x] **Step 5: 运行身份测试并确认 GREEN**

```bash
conda run -n ai_report python -m unittest tests.test_project_identity -v
```

预期：两项测试通过。

### Task 4: 更新活动文档并删除历史 OpenSpec

**Files:**
- Modify: `docs/design/algorithm-scheduling-platform-detailed-design.md`
- Modify: `docs/superpowers/plans/2026-08-03-算子项目统一结构整改实施计划.md`
- Delete: `text_analysis/openspec/`

- [x] **Step 1: 更新调度设计中的当前项目名**

将 `docs/design/algorithm-scheduling-platform-detailed-design.md` 中三处作为当前服务路径使用的 `llm_api_refactor` 改为 `text_analysis`。接口路径和“第一版只注册两个接口”的调度边界保持不变。

- [x] **Step 2: 更新前一轮迁移结果说明**

将前一轮实施状态中的“`llm_api_refactor/.git` 未改动”改为：文本分析服务后续已作为第八个算子迁移为 `text_analysis`，其 Git 元数据由本计划处理。旧名到新名的历史说明保留。

- [x] **Step 3: 删除项目级历史 OpenSpec**

删除前再次运行：

```bash
rg -n 'openspec' text_analysis/app text_analysis/Dockerfile text_analysis/start.sh text_analysis/config.toml text_analysis/tests text_analysis/scripts text_analysis/requirements.txt
```

预期：无运行时引用。随后将 `text_analysis/openspec/` 移出项目到明确的 `/tmp` 临时备份位置，并确认项目内目录不存在。

- [x] **Step 4: 搜索活动旧路径**

搜索业务源码、README、Docker、Shell、根 `AGENTS.md` 和活动设计文档。除本次设计/计划中的历史名称映射外，不得再把 `llm_api_refactor` 作为当前路径或项目名。

### Task 5: 最终验证并移除 Git 元数据

**Files:**
- Delete: `text_analysis/.git/`

- [x] **Step 1: 编译、测试和依赖检查**

从 `text_analysis/` 执行：

```bash
conda run -n ai_report python -m compileall -q app
conda run -n ai_report python -m unittest discover -s tests -v
conda run -n ai_report python -m pip check
```

预期：编译成功；现有测试无失败；依赖检查无破损。

- [x] **Step 2: 比对入口和路由基线**

重新导入 `app.main:app` 并生成完整路由清单，与 `/tmp/text-analysis-rename-20260803/routes.txt` 比较。预期路径和 HTTP 方法完全一致，唯一允许变化是 OpenAPI 应用标题。

- [x] **Step 3: 核对结构和用户文件**

确认 `text_analysis/app/main.py`、README、`AGENTS.md`、配置、Prompt、测试、Docker 和脚本存在，`llm_api_refactor/` 与 `text_analysis/openspec/` 不存在。使用迁移前状态和目录基线确认无其他用户文件丢失。

- [x] **Step 4: 移除项目 Git 元数据**

创建唯一 `/tmp/text-analysis-git-backup.XXXXXX` 目录，将 `text_analysis/.git` 移入该目录。不得删除或修改其他路径中的 Git 元数据，不得初始化根仓库。

- [x] **Step 5: 删除后复验**

再次确认：

- `text_analysis/.git` 不存在。
- 工作区根目录不是 Git 仓库。
- 八个算子目录内均不存在 `.git`。
- `text_analysis` 的 `app.main:app` 和完整路由仍可导入。
- 本轮没有 Uvicorn 服务残留监听 `8000`。

在实施计划中记录完成状态、Git 临时备份路径和 OpenSpec 临时备份路径。

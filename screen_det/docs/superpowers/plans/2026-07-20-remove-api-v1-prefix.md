# Remove API v1 Prefix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除所有 `/api/v1/*` 路由和 `api_prefix` 配置，只保留无前缀接口。

**Architecture:** 保持 `app.api.v1.router` 内部模块组织不变，只在 FastAPI 应用层挂载一次公共路由。通过路由与配置回归测试锁定无前缀接口可用、旧前缀接口返回 404，并同步所有调用文档和部署验证脚本。

**Tech Stack:** Python 3.11、FastAPI、unittest、TOML、Conda `screen_det`

---

### Task 1: 用失败测试定义无前缀路由契约

**Files:**
- Create: `tests/test_routes.py`
- Modify: `tests/test_aggregate_detection.py`

- [x] **Step 1: 新增路由和配置回归测试**

```python
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


ROOT = Path(__file__).resolve().parents[1]


class RoutePrefixTests(unittest.TestCase):
    def test_root_advertises_only_unprefixed_routes(self) -> None:
        body = TestClient(app).get("/").json()
        self.assertEqual("/health", body["health"])
        self.assertEqual("/detect_all", body["detect_all"])
        self.assertTrue(all(not value.startswith("/api/v1") for key, value in body.items() if key not in {"service", "version"}))

    def test_api_v1_routes_are_not_registered(self) -> None:
        client = TestClient(app)
        for path in ("/api/v1/health", "/api/v1/detect_all", "/api/v1/config"):
            with self.subTest(path=path):
                self.assertEqual(404, client.get(path).status_code)

    def test_api_prefix_configuration_is_removed(self) -> None:
        self.assertFalse(hasattr(get_settings().app, "api_prefix"))
        self.assertNotIn("api_prefix", (ROOT / "config.toml").read_text(encoding="utf-8"))
```

将 `tests/test_aggregate_detection.py::test_detect_all_api_v1_route_exists` 改为无前缀路由覆盖测试，避免保留相反的旧契约。

- [x] **Step 2: 运行测试并确认 RED**

Run:

```bash
conda run --no-capture-output -n screen_det python -m unittest tests.test_routes
```

Expected: FAIL，原因包括根路径仍返回 `/api/v1/...`、旧前缀路由仍可访问、`api_prefix` 配置仍存在。

### Task 2: 删除前缀路由和配置

**Files:**
- Modify: `app/main.py`
- Modify: `app/core/config.py`
- Modify: `config.toml`
- Test: `tests/test_routes.py`

- [x] **Step 1: 只保留一次无前缀路由挂载**

在 `app/main.py` 删除：

```python
app.include_router(v1_router, prefix=settings.app.api_prefix)
```

保留：

```python
app.include_router(v1_router)
```

并将根路径响应中的接口地址改为 `/health`、`/detect_tilt`、`/detect_screen`、`/detect_inspect`、`/detect_all`、`/detect_quality_abnormal`、`/detect_occlusion`。

- [x] **Step 2: 删除 api_prefix 配置**

从 `AppConfig` 删除：

```python
api_prefix: str = "/api/v1"
```

从 `config.toml` 的 `[app]` 删除 `api_prefix = "/api/v1"` 及对应注释。

- [x] **Step 3: 运行路由测试并确认 GREEN**

Run:

```bash
conda run --no-capture-output -n screen_det python -m unittest tests.test_routes tests.test_aggregate_detection
```

Expected: PASS，并确认 `/api/v1/*` 为 404。

### Task 3: 同步文档和部署验证脚本

**Files:**
- Modify: `README.md`
- Modify: `docs/API接口文档.md`
- Modify: `AGENT.md`
- Modify: `scripts/deploy_verify_http.py`

- [x] **Step 1: 将文档接口统一为无前缀地址**

删除“双路由等价”的说明和所有 `/api/v1` 示例。配置章节删除：

```toml
api_prefix = "/api/v1"
```

明确服务只提供 `/health`、`/detect_tilt`、`/detect_screen`、`/detect_inspect`、`/detect_quality_abnormal`、`/detect_occlusion`、`/detect_all`、`/config` 和 `/config/reload`。

- [x] **Step 2: 部署验证脚本只调用无前缀路由**

把三个双路径循环分别改为单路径调用：

```python
for path in ("/detect_quality_abnormal",):
for path in ("/detect_occlusion",):
for path in ("/detect_all",):
```

- [x] **Step 3: 扫描残留**

Run:

```bash
rg -n '/api/v1|api_prefix' app config.toml scripts README.md docs/API接口文档.md AGENT.md
rg -n '/api/v1|api_prefix' tests
```

Expected: 第一条命令无输出；第二条命令只包含验证旧路径返回 404、配置字段已删除的负向断言。

### Task 4: 全量验证

**Files:**
- Verify only

- [x] **Step 1: 运行全部单元测试**

Run:

```bash
conda run --no-capture-output -n screen_det python -m unittest discover
```

Expected: 所有测试通过，0 failures，0 errors。

- [x] **Step 2: 运行编译和差异检查**

Run:

```bash
conda run --no-capture-output -n screen_det python -m compileall app scripts tests
git diff --check
```

Expected: 两条命令退出码均为 0。

- [x] **Step 3: 检查最终差异和工作区**

Run:

```bash
git diff --stat
git status --short --branch
```

Expected: 仅包含本次路由、配置、测试和文档变更，不包含模型或检测算法改动。

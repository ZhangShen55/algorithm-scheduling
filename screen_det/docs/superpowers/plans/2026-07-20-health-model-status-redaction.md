# Health Model Status Redaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从`GET /health`公开响应的两个模型状态中移除`weights`字段。

**Architecture:** 模型holder继续保留完整内部状态。健康接口复制状态字典后仅移除公开响应中的`weights`，ready判定和其他运行信息保持不变。

**Tech Stack:** Python 3.11、FastAPI、unittest

---

### Task 1: 健康接口模型状态脱敏

**Files:**
- Modify: `tests/test_model_startup.py`
- Modify: `app/api/v1/health.py`
- Modify: `README.md`
- Modify: `docs/API接口文档.md`

- [x] **Step 1: 编写失败测试**

在健康检查测试的holder状态中加入`weights`，并断言响应的两个模型对象都不包含该键：

```python
self.assertNotIn("weights", body["screen_model"])
self.assertNotIn("weights", body["occlusion_model"])
```

- [x] **Step 2: 运行测试确认RED**

```bash
conda run --no-capture-output -n screen_det python -m unittest \
  tests.test_model_startup.ModelStartupTests.test_health_hides_model_weights
```

预期：FAIL，当前响应仍包含`weights`。

- [x] **Step 3: 实现公开状态脱敏**

在`app/api/v1/health.py`中复制holder状态并移除字段：

```python
screen_model = {key: value for key, value in screen_model_holder.status.items() if key != "weights"}
occlusion_model = {
    key: value for key, value in occlusion_model_holder.status.items() if key != "weights"
}
```

- [x] **Step 4: 运行定向测试确认GREEN**

```bash
conda run --no-capture-output -n screen_det python -m unittest tests.test_model_startup
```

预期：全部通过。

- [x] **Step 5: 运行全量验证**

```bash
conda run --no-capture-output -n screen_det python -m unittest discover
conda run --no-capture-output -n screen_det python -m compileall app tests
git diff --check
```

预期：全部退出码0。

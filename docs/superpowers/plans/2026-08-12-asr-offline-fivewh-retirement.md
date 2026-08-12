# Offline ASR FiveWh Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 `asr_offline` 移除五何分析及其 BERT 运行时资源，并将保留的 v1.1.8 路由模块由 `asr_v18.py` 收敛为 `asr.py`。

**Architecture:** 只退役 `asr_offline` 的 `POST /text/question`，不改独立 `text_analysis` 算子。公开的 `POST /v1.1.8/seacraft_asr`、处理函数名和响应合同保持不变；通过契约测试、配置形状测试和 Docker 上下文排除规则证明 BERT/FiveWh 不再进入运行时或镜像。

**Tech Stack:** Python 3.11、FastAPI、unittest、pytest、TOML、Docker ignore、Harness Markdown。

---

### Task 1: 建立退役合同的失败测试

**Files:**
- Create: `asr_offline/tests/test_fivewh_retirement.py`
- Modify: `algorithm-scheduling-platform/tests/test_milestone_2b_operator_configs.py`
- Modify: `algorithm-scheduling-platform/tests/test_operator_deployment_integration.py`

- [x] **Step 1: 写入路由、文件、配置和镜像资源断言**

  断言 `/text/question` 不在 OpenAPI 且 HTTP POST 返回 404，`/v1.1.8/seacraft_asr` 仍存在；断言 `app/api/routes/asr.py` 存在，`asr_v18.py` 和 `text.py` 不存在；断言 FiveWh/BERT 标识符和配置键消失，并要求 `.dockerignore` 排除两个本地 BERT 目录。

- [x] **Step 2: 运行测试并确认按预期失败**

  Run: `cd asr_offline && conda run -n asr python -m unittest tests.test_fivewh_retirement -v`

  Expected: FAIL，失败原因是当前 `/text/question` 仍注册、`asr.py` 尚不存在且 FiveWh/BERT 配置仍保留。

  Run: `cd algorithm-scheduling-platform && .venv/bin/python -m pytest -q tests/test_milestone_2b_operator_configs.py tests/test_operator_deployment_integration.py`

  Expected: FAIL，失败原因是部署合同仍指向 `asr_v18.py` 且配置仍保留 BERT/FiveWh。

### Task 2: 删除 FiveWh 运行时并重命名路由模块

**Files:**
- Rename: `asr_offline/app/api/routes/asr_v18.py` -> `asr_offline/app/api/routes/asr.py`
- Delete: `asr_offline/app/api/routes/text.py`
- Modify: `asr_offline/app/main.py`
- Modify: `asr_offline/app/core/models.py`
- Modify: `asr_offline/app/core/config.py`
- Modify: `asr_offline/app/entity/data.py`
- Modify: `asr_offline/app/utils/feature_utils.py`
- Modify: `asr_offline/tests/test_gpu_runtime.py`
- Modify: `asr_offline/tests/test_v118_multilingual.py`

- [x] **Step 1: 收敛保留的 ASR 路由模块**

  将模块重命名为 `asr.py`，在 `app.main` 和测试中改用 `app.api.routes.asr`；保留 `api_asr_v18` 函数名、HTTP 路径和生成的 operationId。

- [x] **Step 2: 删除 FiveWh 路由、数据模型、特征整理和 BERT 推理代码**

  删除 `text.py` 及其 router 注册，删除 `Segment`、`SegmentRequestBody`、FiveWh 专属 helpers、BERT imports/cache/load/predict 代码；保留 ASR、说话人、情绪和 Whisper 代码。

- [x] **Step 3: 更新 GPU 测试并运行算子测试**

  删除 BERT mock/cache/guard 测试，将启动测试改为只验证 Paraformer、emotion2vec 和 Whisper。

  Run: `cd asr_offline && conda run -n asr python -m unittest discover -s tests -v`

  Expected: PASS，且没有 FiveWh/BERT 测试或导入。

### Task 3: 删除配置并阻止 BERT 模型进入镜像

**Files:**
- Modify: `asr_offline/config.toml`
- Modify: `asr_offline/.dockerignore`
- Modify: `algorithm-scheduling-platform/deploy/config/operators/asr_offline.gpu.toml`
- Modify: `algorithm-scheduling-platform/tests/test_milestone_2b_operator_configs.py`

- [x] **Step 1: 删除两个 BERT 路径和 `open_fivewh`**

  同步维护算子源配置与平台共享配置的相同键形状，只保留 Paraformer、VAD、标点、CAM++、emotion2vec 和 Whisper 路径及其现有功能开关。

- [x] **Step 2: 排除本地大模型目录**

  在 `.dockerignore` 明确加入 `model/bert-base-chinese/` 和 `model/bert_output/`；不物理删除被 Git 忽略的本地模型资产。

- [x] **Step 3: 运行配置与部署合同测试**

  Run: `cd algorithm-scheduling-platform && .venv/bin/python -m pytest -q tests/test_milestone_2b_operator_configs.py tests/test_operator_deployment_integration.py`

  Expected: PASS，部署合同只引用 `app/api/routes/asr.py`。

### Task 4: 更新当前文档和 Harness 证据

**Files:**
- Modify: `asr_offline/README.md`
- Modify: `asr_offline/AGENTS.md`
- Modify: `algorithm-scheduling-platform/harness/change-ledger.md`
- Modify: `algorithm-scheduling-platform/harness/scenarios/operator-local-runtime-validation.md`
- Modify: `algorithm-scheduling-platform/harness/verification.md`

- [x] **Step 1: 更新当前运行说明**

  删除 FiveWh 能力、接口、配置和模型说明，将源文件树改为 `asr.py`，明确三个退役接口均应从 OpenAPI 消失并返回 404。

- [x] **Step 2: 新增独立 Harness 变更记录**

  在 ledger 顶部新增 FiveWh 退役条目；场景文档记录资源边界，verification 增加 `/text/question` 的 OpenAPI/404 检查和本轮实测计数。历史设计和架构评审不回写。

- [x] **Step 3: 运行 Harness 一致性测试**

  Run: `cd algorithm-scheduling-platform && .venv/bin/python -m pytest -q tests/test_harness_consistency.py`

  Expected: PASS。

### Task 5: 完整验证和中文规范提交

**Files:**
- Verify: `asr_offline/app/`
- Verify: `asr_offline/tests/`
- Verify: `algorithm-scheduling-platform/tests/`

- [x] **Step 1: 执行静态、完整单元和依赖验证**

  Run: `cd asr_offline && conda run -n asr python -m compileall -q app && conda run -n asr python -c 'from app.main import app; print(app.title)' && conda run -n asr python -m unittest discover -s tests -v && conda run -n asr python -m pip check`

  Expected: 全部退出码为 0。

- [x] **Step 2: 执行平台聚焦回归**

  Run: `cd algorithm-scheduling-platform && .venv/bin/python -m pytest -q tests/test_milestone_2b_operator_configs.py tests/test_operator_deployment_integration.py tests/test_harness_consistency.py`

  Expected: 全部通过。

- [x] **Step 3: 冷启动、HTTP 合同和真实推理**

  在未占用端口启动 `app.main:app`，验证 `/ops/health` 为 200，v1.1.8 存在，`/text/question`、v1.1.7 和普通话检测均为 404；使用 `test_wav/chinEng-16k.wav` 完成真实推理。已有法语响应算法未变化时，不重复耗时约九分钟的法语推理，但保留先前 Harness 证据。

- [x] **Step 4: 独立代码评审和提交**

  运行 `git diff --check`、残留标识扫描并请求独立 reviewer。按显式文件清单提交代码/配置/测试为 `refactor(asr)!: 移除五何分析并收敛路由模块`，并用 `BREAKING CHANGE:` footer 说明 `POST /text/question` 退役；再提交当前文档/Harness 为 `docs(harness): 记录离线ASR五何能力退役`。不暂存无关文件。

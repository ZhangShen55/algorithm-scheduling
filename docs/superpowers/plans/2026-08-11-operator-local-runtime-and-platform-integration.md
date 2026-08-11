# 算子本机运行与平台集成实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 形成可独立安装的算子注册客户端，完成 FaceRec 图片留存开关、ASR Python 3.11 迁移和 FaceRec 3.11 兼容性审计，并用本机真实算子验证调度平台的接入前提。

**Architecture:** 算子注册客户端作为轻量、版本化 wheel 独立交付，只依赖 FastAPI、HTTPX 和 Pydantic，不把 PostgreSQL、Redis 或调度仓储依赖带入算子。FaceRec 继续拥有 MongoDB 人脸特征库，A 服务通过平台北向管理接口访问；`save_person_photo=false` 只禁止裁剪图落盘，不改变 embedding 生成、入库和识别。所有算子仍以一个容器、一个 Uvicorn worker、一个可注册端点运行。

**Tech Stack:** Python 3.10/3.11、FastAPI、Pydantic v2、HTTPX、Setuptools/Wheel、Conda、Pytest、Docker、MongoDB。

---

### Task 1: 审计平台目录中的可重建文件

**Files:**
- Create: `algorithm-scheduling-platform/docs/本地可重建文件清单.md`
- Modify: `.gitignore`
- Test: `algorithm-scheduling-platform/tests/test_repository_layout.py`

- [ ] **Step 1: 写入失败测试**

增加断言，要求清单明确区分“可直接删除缓存”“可重建但当前仍有用途的虚拟环境”“不得删除的源码、迁移、部署、Harness 和测试”。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest algorithm-scheduling-platform/tests/test_repository_layout.py -q`

Expected: FAIL，因为清单文件尚不存在。

- [ ] **Step 3: 写入最小清单与忽略规则**

清单必须列出 `.mypy_cache/`、`.pytest_cache/`、`.ruff_cache/`、`__pycache__/`、`*.egg-info/`、`.DS_Store`，并注明 `.venv/` 可重建但当前是平台开发环境，不纳入本轮自动删除。

- [ ] **Step 4: 删除精确确认的缓存并验证 Git 状态**

只删除 Git 已忽略的缓存目录和 `.DS_Store`，保留 `.venv/`、源码、迁移、部署、Harness、测试和用户产物。

Run: `git status --short --ignored algorithm-scheduling-platform`

Expected: 只剩允许保留的忽略项，不出现已跟踪文件删除。

- [ ] **Step 5: 运行测试**

Run: `python -m pytest algorithm-scheduling-platform/tests/test_repository_layout.py -q`

Expected: PASS。

### Task 2: 构建可独立安装的算子注册客户端 wheel

**Files:**
- Create: `algorithm-scheduling-platform/packages/operator_registry_client/pyproject.toml`
- Create: `algorithm-scheduling-platform/packages/operator_registry_client/tests/test_isolated_wheel.py`
- Modify: `algorithm-scheduling-platform/packages/operator_registry_client/ops.py`
- Modify: `algorithm-scheduling-platform/packages/operator_registry_client/runtime.py`
- Modify: `algorithm-scheduling-platform/packages/operator_registry_client/README.md`
- Modify: `algorithm-scheduling-platform/tests/test_operator_registry_client.py`

- [ ] **Step 1: 写入 wheel 元数据和隔离安装失败测试**

测试在临时虚拟环境中构建并安装 wheel，然后执行：

```python
from packages.operator_registry_client import install_operator_runtime
assert callable(install_operator_runtime)
```

同时断言 wheel 的运行依赖仅包含 `fastapi`、`httpx` 和 `pydantic`。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest algorithm-scheduling-platform/packages/operator_registry_client/tests/test_isolated_wheel.py -q`

Expected: FAIL，因为注册客户端还没有独立构建配置，且仍依赖 `packages.platform_common`。

- [ ] **Step 3: 移除平台内部枚举依赖并补充独立构建配置**

在客户端包内声明等价的 `OperatorLifecycle(str, Enum)`，保持 `ONLINE`、`DRAINING`、`OFFLINE` 字符串契约不变；wheel 名称使用 `algorithm-operator-registry-client`，版本 `0.1.0`，Python 要求 `>=3.10`，以兼容经审计必须保留 Python 3.10 的 FaceRec。

- [ ] **Step 4: 运行隔离安装与现有注册测试**

Run: `python -m pytest algorithm-scheduling-platform/packages/operator_registry_client/tests/test_isolated_wheel.py algorithm-scheduling-platform/tests/test_operator_registry_client.py -q`

Expected: PASS。

- [ ] **Step 5: 构建正式 wheel 并核对内容**

Run: `python -m build --wheel algorithm-scheduling-platform/packages/operator_registry_client`

Expected: `dist/algorithm_operator_registry_client-0.1.0-py3-none-any.whl`，且 wheel 中只包含注册客户端包及元数据。

### Task 3: 将注册客户端纳入算子本机与镜像安装契约

**Files:**
- Modify: `algorithm-scheduling-platform/tests/test_operator_deployment_integration.py`
- Modify: `algorithm-scheduling-platform/deploy/README.md`
- Modify: `asr_offline/docker/Dockerfile`
- Modify: `asr_online/docker/Dockerfile`
- Modify: `asr_online/docker/Dockerfile.cython`
- Modify: `facerec/docker/Dockerfile`
- Modify: 其余算子的规范 Dockerfile 与运行依赖文件
- Modify: `algorithm-scheduling-platform/scripts/stage_operator_registry_wheel.py`
- Modify: 各算子的 `README.md` 中注册客户端安装说明

- [ ] **Step 1: 写入失败契约测试**

断言八个算子的 requirements 固定 `algorithm-operator-registry-client==0.1.0`，规范镜像安装同一个已暂存 wheel，且禁止源码挂载和 `PYTHONPATH`。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest algorithm-scheduling-platform/tests/test_operator_deployment_integration.py -q`

Expected: FAIL，因为当前 Dockerfile 没有安装 wheel。

- [ ] **Step 3: 更新镜像和本机安装说明**

内部 PyPI 尚未建立时，脚本将本轮构建的固定版本 wheel 暂存到八个算子的 Git 忽略构建目录；镜像先安装 wheel，再解析 requirements。本机同样从该 wheel 安装。保留所有业务路由、端口和 `--workers 1`。

- [ ] **Step 4: 运行契约测试**

Run: `python -m pytest algorithm-scheduling-platform/tests/test_operator_deployment_integration.py -q`

Expected: PASS。

### Task 4: 为 FaceRec 增加禁止人物裁剪图落盘的配置

**Files:**
- Create: `facerec/tests/test_person_photo_persistence.py`
- Modify: `facerec/app/core/config.py`
- Modify: `facerec/config.toml`
- Modify: `facerec/app/router/persons.py`
- Modify: `facerec/README.md`

- [ ] **Step 1: 写入单人和批量失败测试**

测试必须证明：`save_person_photo=false` 时不调用 `cv2.imwrite`、数据库仍收到非空 embedding、`photo_path` 为空且响应为业务成功；`true` 时维持现有写图行为。

- [ ] **Step 2: 运行测试并确认失败**

Run: `conda run -n facerecapi python -m pytest -q tests/test_person_photo_persistence.py`

Expected: FAIL，因为配置字段和条件分支尚不存在。

- [ ] **Step 3: 实现最小配置与共享保存函数**

在 `[image]` 增加 `save_person_photo = false`。单人和批量接口共用保存函数；关闭时返回空路径但不产生 `FILE_SAVE_ERROR`，开启且写图失败时保持原错误语义。

- [ ] **Step 4: 运行 FaceRec 单元测试**

Run: `conda run -n facerecapi python -m pytest -q tests/test_person_photo_persistence.py tests/test_device_config.py`

Expected: PASS。

### Task 5: 统一 ASR Python 3.11 并审计 FaceRec 3.11 兼容性

**Files:**
- Modify: `asr_offline/docker/Dockerfile`
- Modify: `asr_online/docker/Dockerfile`
- Modify: `asr_online/docker/Dockerfile.cython`
- Modify: `asr_online/docker/start.sh`
- Modify: `facerec/docker/Dockerfile`
- Modify: `asr_offline/AGENTS.md`
- Modify: `asr_online/AGENTS.md`
- Modify: `facerec/AGENTS.md`

- [ ] **Step 1: 写入 Python 与环境名契约测试**

断言三个 ASR Dockerfile 使用 Python 3.11 和环境名 `asr`；FaceRec 环境名保持 `facerecapi`，Python 版本由 FastDeploy 二进制兼容性审计决定。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest algorithm-scheduling-platform/tests/test_operator_deployment_integration.py -q`

Expected: FAIL，当前 ASR Online 使用 `seacraftasr_online` 且镜像仍是 Python 3.10。

- [ ] **Step 3: 更新 Dockerfile 与启动脚本**

仅更新经过真实推理验证的 Python 版本和环境名，不改变模型、业务接口、默认端口和单 worker 约束；ASR Offline 的依赖必须来自可验证的 Python 3.11 安装来源。FaceRec 若缺少 cp311 原生扩展则保留 Python 3.10，不得静默更换推理后端。

- [ ] **Step 4: 克隆临时环境并升级验证**

Run: `conda create -n asr-py311 --clone asr && conda install -n asr-py311 python=3.11`

Run: `conda create -n facerecapi-py311 --clone facerecapi && conda install -n facerecapi-py311 python=3.11`

Expected: ASR 临时环境报告 Python 3.11 且真实推理通过；FaceRec 若在导入阶段暴露原生扩展 ABI 不兼容，记录根因并回退到原 `facerecapi` Python 3.10 环境。

- [ ] **Step 5: 完整验证后进行可恢复改名**

先导出原环境清单；临时环境通过编译、导入、测试、启动和真实推理后，再生成最终环境。最终名称必须为 `asr` 和 `facerecapi`，版本分别按已验证兼容性落定。

### Task 6: 顺序验证全部算子真实运行

**Files:**
- Create: `algorithm-scheduling-platform/harness/scenarios/operator-local-runtime-validation.md`
- Modify: `algorithm-scheduling-platform/harness/verification.md`
- Modify: `algorithm-scheduling-platform/harness/change-ledger.md`

- [ ] **Step 1: 对每个算子运行静态验证**

依次运行 `compileall`、`from app.main import app`、项目测试和 `pip check`，使用根 `AGENTS.md` 指定环境。

- [ ] **Step 2: 逐个启动并检查健康/就绪**

使用端口 `8083`、`8084`、`8003`、`8866`、`8880`、`9001`、`8981`、`8000`，每次确认进程退出后再启动下一个重模型算子。

- [ ] **Step 3: 运行真实推理**

ASR 使用仓库 WAV，FaceRec 使用 `tests/data/常泽宇.png`，OCR/ScreenDet/VBas 使用各自 fixture，PPT 使用最新 `video_path` 合同和本地绝对路径，文本分析使用真实 ASR/OCR 结果及已验证的 Qwen 接口。

- [ ] **Step 4: 记录达到的验证层级与缺口**

每个算子分别记录命令、输入、HTTP/WebSocket 路径、业务状态、结果摘要、耗时和失败原因。缺少外部数据时只标记对应算子未完成，不用模拟成功替代真实推理。

### Task 7: 复核 PPT 最新合同并锁定平台适配器

**Files:**
- Modify: `algorithm-scheduling-platform/tests/test_ppt_slice_adapter.py`
- Modify: `orchestrator_service/app/infrastructure/ppt_slice.py`
- Modify: `algorithm-scheduling-platform/harness/scenarios/ppt-shared-result.md`

- [ ] **Step 1: 读取当前分支最新提交并写入失败契约测试**

平台只发送绝对本地 `video_path`、`task_id`、`operator_task_id` 和一次终态 `result_callback_uri`；结果读取 `/data/result/{task_id}/ppt/manifest.json`，保留 `dynamic_segments`。

- [ ] **Step 2: 运行测试并确认实际状态**

Run: `python -m pytest algorithm-scheduling-platform/tests/test_ppt_slice_adapter.py ppt_slice/tests/test_api_contract.py ppt_slice/tests/test_shared_results.py -q`

Expected: 若适配器仍偏离最新合同则 FAIL；已一致的断言不得人为制造失败。

- [ ] **Step 3: 只修改平台适配器差异**

不得覆盖 PPT 另一会话的算法实现；只依据已提交代码修正调度侧字段、manifest 和终态处理。

- [ ] **Step 4: 运行 PPT 合同回归**

Run: `python -m pytest algorithm-scheduling-platform/tests/test_ppt_slice_adapter.py ppt_slice/tests/test_api_contract.py ppt_slice/tests/test_shared_results.py -q`

Expected: PASS。

### Task 8: 复审人脸库北向边界与分支集成

**Files:**
- Modify: `docs/算法功能调度平台总体设计-v2.md`
- Modify: `algorithm-scheduling-platform/harness/architecture-review.md`
- Modify: `algorithm-scheduling-platform/harness/change-ledger.md`

- [ ] **Step 1: 记录人脸库边界**

FaceRec/MongoDB 是人脸 embedding 的领域权威；A 服务通过 `online-gateway-service` 的独立人脸库管理路由访问，不直接连接 MongoDB，也不把人脸库写入平台 PostgreSQL。初期不增加第五个服务；认证、审计或扩容边界独立后再拆分。

- [ ] **Step 2: 复审四服务边界**

确认人脸库代理不进入离线 Kafka、不占用课程任务 DAG，也不改变在线识别的实例租约路径。

- [ ] **Step 3: 等待并保护已有里程碑一工作区**

不得提交或覆盖 `.worktrees/control-service-foundation-closure` 的未提交文档。仅在该工作区由其所有者提交后，整合 `8d1121a` 及后续文档提交。

- [ ] **Step 4: 运行全量验证**

Run: `python -m pytest -q`

Run: `python -m ruff check .`

Expected: 所有不依赖外部基础设施的测试通过；PostgreSQL、Redis、Kafka 和真实算子项分别报告实际验证层级。

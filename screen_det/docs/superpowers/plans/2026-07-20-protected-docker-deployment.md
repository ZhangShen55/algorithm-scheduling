# Protected Docker Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 建立Cython源码保护、AES-GCM模型保护、统一YOLO设备配置和集中式Docker生产部署目录。

**Architecture:** 开发环境继续加载`model/*.pt`；生产环境从只读挂载解密模型到`/dev/shm`，完成两个YOLO对象加载与预热后删除临时明文。Docker多阶段构建将业务模块编译为`.so`，运行层只保留极薄入口、运行依赖和启动脚本。

**Tech Stack:** Python 3.11、FastAPI、Ultralytics YOLO、PyTorch、cryptography AESGCM、Cython 3、Docker、unittest

---

### Task 1: 模型加密与临时解密

**Files:**
- Create: `app/core/model_protection.py`
- Create: `tests/test_model_protection.py`
- Modify: `requirements.txt`
- Modify: `requirements-docker.txt`，Task 5移动后路径为`docker/requirements-docker.txt`

- [x] **Step 1: 编写AES-GCM失败测试**

测试固定覆盖`generate_key()`、加解密往返、错误密钥、损坏密文、缺失密钥、明文模式和加密模式临时文件清理。模型路径解析使用：

```python
with materialize_model_path(Path("model/screen.pt"), config) as path:
    assert path.read_bytes() == plaintext
assert not path.exists()
```

- [x] **Step 2: 运行测试确认RED**

```bash
conda run --no-capture-output -n screen_det python -m unittest tests.test_model_protection
```

Expected: FAIL，`app.core.model_protection`不存在。

- [x] **Step 3: 实现模型保护模块**

实现`ModelProtectionError`、`ModelProtectionConfig`、`generate_key()`、`encrypt_model_file()`、`decrypt_model_file()`、`read_key_file()`和`materialize_model_path()`。加密格式为固定magic、12字节nonce和AES-256-GCM密文；加密模式只写入配置的`decrypted_temp_root`并在context退出时删除。

- [x] **Step 4: 增加运行依赖并确认GREEN**

在本地与Docker依赖中加入：

```text
cryptography>=42.0.0,<46
```

重新运行`tests.test_model_protection`，Expected: PASS。

### Task 2: 统一YOLO设备配置

**Files:**
- Modify: `app/core/config.py`
- Modify: `config.toml`
- Modify: `app/services/screen_detector.py`
- Modify: `app/services/occlusion_detector.py`
- Modify: `app/services/aggregate_detector.py`
- Modify: `app/api/v1/config.py`
- Modify: `app/api/v1/health.py`
- Modify: `tests/test_quality_occlusion.py`
- Modify: `tests/test_aggregate_detection.py`
- Create: `tests/test_yolo_runtime.py`

- [x] **Step 1: 编写统一配置与严格设备测试**

断言配置只存在：

```python
settings.yolo.device
```

并确认不存在`settings.gpu`、`occlusion_detection.yolo_device`和`aggregate_detection.device`。使用mock torch验证`cpu`、`mps`、`cuda:0`，并验证CUDA不可用或索引越界抛出`RuntimeError`。

- [x] **Step 2: 运行相关测试确认RED**

```bash
conda run --no-capture-output -n screen_det python -m unittest tests.test_yolo_runtime tests.test_quality_occlusion tests.test_aggregate_detection
```

Expected: FAIL，统一`YoloConfig`和设备解析函数不存在。

- [x] **Step 3: 实现统一配置**

新增：

```python
@dataclass(frozen=True)
class YoloConfig:
    device: str = "cpu"
```

同时将`ModelProtectionConfig`加入`Settings`。删除旧三个device来源，所有模型holder和聚合响应改读`settings.yolo.device`。

- [x] **Step 4: 实现严格设备解析**

`resolve_yolo_device()`只接受`cpu`、`mps`和`cuda:<index>`；请求CUDA但不可用时抛错，不回退CPU。聚合接口继续返回统一device。

- [x] **Step 5: 实现启动配置重载保护**

`/config/reload`在`yolo.device`、模型保护配置或模型权重路径变化时返回HTTP 409，不清理任何模型缓存；阈值变化仍可热加载。

- [x] **Step 6: 运行相关测试确认GREEN**

重新运行Task 2测试，Expected: PASS。

### Task 3: 两个YOLO模型完整启动加载

**Files:**
- Modify: `app/services/screen_detector.py`
- Modify: `app/services/occlusion_detector.py`
- Modify: `app/main.py`
- Create: `app/application.py`
- Modify: `app/api/v1/health.py`
- Create: `tests/test_model_startup.py`

- [x] **Step 1: 编写启动生命周期失败测试**

mock两个holder，验证启动依次加载并预热screen和occlusion；任一失败时startup抛错；健康检查只有两者均`loaded=True`且`warmed_up=True`才ready。

- [x] **Step 2: 运行测试确认RED**

```bash
conda run --no-capture-output -n screen_det python -m unittest tests.test_model_startup
```

Expected: FAIL，当前启动只预加载screen。

- [x] **Step 3: 接入模型临时解密和预热**

两个holder均通过`materialize_model_path()`获得YOLO加载路径，构造`YOLO`后执行一次dummy推理并退出context清理临时明文。holder只保存逻辑模型名、内存模型、统一device和warmed状态，不保存临时文件依赖。

- [x] **Step 4: 拆分极薄入口**

将FastAPI应用装配移动到`app/application.py`，`app/main.py`只保留：

```python
from app.application import app

__all__ = ["app"]
```

- [x] **Step 5: 运行启动和完整单元测试**

```bash
conda run --no-capture-output -n screen_det python -m unittest tests.test_model_startup
conda run --no-capture-output -n screen_det python -m unittest discover
```

Expected: PASS。

### Task 4: 模型保护命令

**Files:**
- Create: `docker/protect_models.py`
- Create: `docker/models-encrypted/.gitignore`
- Modify: `.gitignore`
- Modify: `.dockerignore`
- Create: `tests/test_protect_models_script.py`

- [x] **Step 1: 编写默认模型与Git忽略测试**

断言：

```python
DEFAULT_MODEL_NAMES == ["occlusion.pt", "screen.pt"]
```

并验证脚本从`model/`生成两个`.enc`和权限`0600`的`model.key`；Git和Docker构建上下文忽略密钥、`.enc`和明文`model/`。

- [x] **Step 2: 运行测试确认RED**

```bash
conda run --no-capture-output -n screen_det python -m unittest tests.test_protect_models_script
```

Expected: FAIL，保护脚本不存在。

- [x] **Step 3: 实现保护命令并确认GREEN**

脚本支持`--source-dir`、`--target-dir`、`--key-file`、`--generate-key`和可重复`--model`；默认路径分别为`model/`和`docker/models-encrypted/`。重新运行测试，Expected: PASS。

### Task 5: Docker目录重组和Cython构建

**Files:**
- Delete: `AGENT.md`
- Move: `Dockerfile` -> `docker/Dockerfile`
- Move: `requirements-docker.txt` -> `docker/requirements-docker.txt`
- Move: `start.sh` -> `docker/start.sh`
- Move: `scripts/run_deploy_verify.sh` -> `docker/run_deploy_verify.sh`
- Move: `scripts/deploy_verify_http.py` -> `docker/deploy_verify_http.py`
- Create: `docker/build_cython_modules.py`
- Create: `docker/README.md`
- Modify: `README.md`
- Create: `tests/test_docker_layout.py`

- [x] **Step 1: 编写Docker布局和运行层审计测试**

测试根目录不存在旧Docker文件和`AGENT.md`，`docker/`包含所有部署文件；Dockerfile使用多阶段构建、Cython而非PyArmor、不复制`model/`、最终层不复制requirements或构建脚本。

- [x] **Step 2: 运行测试确认RED**

```bash
conda run --no-capture-output -n screen_det python -m unittest tests.test_docker_layout
```

Expected: FAIL，旧文件仍在根目录。

- [x] **Step 3: 移动部署文件并更新路径**

所有构建命令使用：

```bash
docker build -f docker/Dockerfile -t screen_det:<version> .
```

部署验收脚本从`docker/`解析项目根目录并调用同目录HTTP验收脚本。

- [x] **Step 4: 实现Cython构建器和多阶段镜像**

编译除`__init__.py`和`app/main.py`外的`app`模块，使用`binding=True`、`embedsignature=True`、`annotation_typing=False`；编译完成删除源文件、生成C文件和build目录。运行层复制编译后的app和`docker/start.sh`，不复制模型、配置、密钥和构建材料。

- [x] **Step 5: 更新部署文档并确认GREEN**

README与`docker/README.md`记录双挂载、模型加密命令、启动后删除材料的限制、统一device和重启要求。重新运行布局测试，Expected: PASS。

### Task 6: 完整验证和生产镜像审计

**Files:**
- Verify all changed files

- [x] **Step 1: 运行全量Python验证**

```bash
conda run --no-capture-output -n screen_det python -m unittest discover
conda run --no-capture-output -n screen_det python -m compileall app docker scripts tests
git diff --check
```

Expected: 全部退出码0。

- [x] **Step 2: 生成加密模型**

```bash
conda run --no-capture-output -n screen_det python docker/protect_models.py \
  --generate-key \
  --key-file docker/models-encrypted/model.key
```

Expected: 生成`screen.pt.enc`和`occlusion.pt.enc`，不改变明文模型。

- [x] **Step 3: 构建受保护镜像**

```bash
docker build -f docker/Dockerfile -t screen-det:protected-test .
```

Expected: Cython编译、运行层导入冒烟和镜像构建成功。

- [x] **Step 4: 审计镜像文件系统**

```bash
docker run --rm --entrypoint sh screen-det:protected-test -c \
  "find /app -type f | sort; ! find /app -type f \( -name '*.pt' -o -name '*.enc' -o -name '*.key' -o -name '*.c' -o -name 'requirements*.txt' -o -name 'Dockerfile*' \) | grep ."
```

Expected: 无模型、密钥、构建源和requirements；业务模块为`.so`。

- [x] **Step 5: 启动和删除挂载材料后复测**

使用生产测试配置只读挂载`config.toml`和`docker/models-encrypted/`，等待`/health` ready，调用screen、occlusion和aggregate接口；移走宿主机加密模型与密钥后再次调用，响应继续成功。验证完成后恢复本地生成且已被Git忽略的生产模型材料，并删除测试容器。

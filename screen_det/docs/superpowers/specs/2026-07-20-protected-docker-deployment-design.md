# Docker 生产保护部署设计

## 背景

项目开发环境需要保留 Python 源码和两个明文 YOLO 权重，便于调试、训练和本机测试；生产部署需要提高源码与模型被直接复制的门槛。保护目标是避免生产容器文件系统中长期存在可读业务源码、明文模型、密钥和构建材料，不以抵御宿主机 root、进程内存转储或专业逆向为目标。

本次同时整理 Docker 部署文件、统一 YOLO 设备配置，并删除已无维护价值的 `AGENT.md`。

## 目标目录

```text
docker/
├── Dockerfile
├── requirements-docker.txt
├── start.sh
├── build_cython_modules.py
├── protect_models.py
├── run_deploy_verify.sh
├── deploy_verify_http.py
├── README.md
└── models-encrypted/
    ├── .gitignore
    ├── screen.pt.enc       # 本地生成，不提交 Git
    ├── occlusion.pt.enc    # 本地生成，不提交 Git
    └── model.key           # 本地生成，不提交 Git
```

以下文件移动到 `docker/`：

- `Dockerfile`
- `requirements-docker.txt`
- `start.sh`
- `scripts/run_deploy_verify.sh`
- `scripts/deploy_verify_http.py`

新增 Cython 构建脚本、模型加密脚本和生产部署说明。算法评估、样例验证脚本继续保留在 `scripts/`。`.dockerignore` 保留在项目根目录，因为 Docker 构建上下文仍为项目根目录；其中必须排除 `docker/models-encrypted/*`，确保模型密文和密钥不会被发送到 Docker 构建上下文。

构建命令统一为：

```bash
docker build -f docker/Dockerfile -t screen_det:<version> .
```

## Cython 源码保护

采用“除极薄启动入口和空包文件外，其他项目代码尽量编译为 `.so`”的方案。

- 将当前 `app/main.py` 中的 FastAPI 应用创建、生命周期、中间件和异常处理迁移到固定的可编译模块 `app/application.py`。
- `app/main.py` 只负责导入并暴露 `app`，不包含检测、配置、模型保护或业务规则。
- 编译 `app/api/`、`app/core/`、`app/schemas/`、`app/services/` 以及应用装配模块。
- 允许保留空 `__init__.py` 和极薄 `app/main.py`；这些文件不包含核心逻辑。
- Cython 使用 Python 3、`binding=True`、`embedsignature=True`、`annotation_typing=False`，保证 FastAPI 可以读取路由签名和类型注解，并避免把业务类型注解错误解释为 C 类型约束。
- 编译阶段与运行阶段必须使用相同的 Linux 架构和 Python 3.11 ABI。
- 运行镜像不包含已编译模块对应的 `.py`、`.pyx`、生成的 `.c`、`build/`、Cython、编译器或 PyArmor runtime。
- 现有 PyArmor 构建流程由 Cython 替代，不叠加两套源码保护。

构建后必须生成 OpenAPI、导入全部模块并调用所有 HTTP 路由做冒烟验证，不能只以 `.so` 生成成功作为验收。

## 模型加密

模型加密复用 TIAS 方案的 AES-256-GCM 文件格式与错误处理思路，并在当前项目内提供独立实现，不依赖 `tias` 包。

默认加密模型固定为：

```python
DEFAULT_MODEL_NAMES = ["occlusion.pt", "screen.pt"]
```

开发环境：

- `model/screen.pt` 和 `model/occlusion.pt` 保持明文。
- 模型保护默认关闭，现有 Conda 测试和本机开发不受影响。

生产准备：

- 运行 `docker/protect_models.py`，从 `model/` 读取两个明文模型。
- 输出 `docker/models-encrypted/screen.pt.enc`、`occlusion.pt.enc` 和 `model.key`。
- `docker/models-encrypted/` 中除 `.gitignore` 外的内容不得提交 Git，也不得在 Docker build 阶段复制进镜像。

生产启动：

1. 从只读挂载目录读取加密模型和密钥。
2. 将模型解密到仅位于内存文件系统的 `/dev/shm/screen-det-models/`。
3. 完整构造两个 Ultralytics YOLO 对象，将权重加载到 CPU/GPU，并分别执行一次预热推理。
4. 两个模型均加载和预热成功后，立即删除 `/dev/shm` 中的明文 `.pt` 文件，并释放不再需要的密钥变量。
5. 只有两个模型都 ready 时，服务健康检查才返回 ready。
6. 后续请求只使用进程内存中的模型对象，不再读取模型文件。

屏幕和遮挡模型加载失败时，应用启动失败，不提供部分可用服务。

## 生产挂载与生命周期

容器运行时必须同时挂载生产配置和模型材料：

```bash
docker run --rm \
  --gpus device=0 \
  -p 8880:8880 \
  -v "$PWD/config.toml:/app/config.toml:ro" \
  -v "$PWD/docker/models-encrypted:/run/screen-det/models-encrypted:ro" \
  screen_det:<version>
```

生产 `config.toml` 启用模型保护，并将加密目录和密钥路径指向容器内挂载路径。服务 ready 后，运维可以删除宿主机 `docker/models-encrypted/` 中的加密模型和密钥，当前容器依靠内存中的模型继续推理。

删除挂载材料后的明确限制：

- 当前进程继续运行不受影响。
- 容器不能重启或扩容。
- 模型不能释放后重新加载。
- 重启、扩容或重新部署前必须重新提供完整的 `models-encrypted/`。
- `/config/reload` 不得清空模型缓存，也不得热切换设备或模型保护路径。

## 统一 YOLO 设备配置

新增唯一设备配置：

```toml
[yolo]
device = "cpu"
```

支持 `cpu`、`cuda:<index>`，开发环境可支持 `mps`。统一删除：

- `[gpu]` 配置段。
- `[occlusion_detection].yolo_device`。
- `[aggregate_detection].device`。

屏幕检测、遮挡检测、组合检测和聚合检测均读取 `[yolo].device`。倾斜和画面异常 OpenCV 逻辑始终使用 CPU。

当配置为 `cuda:0` 但 CUDA 不可用、索引越界或模型无法迁移到该设备时，服务直接启动失败，不允许静默回退 CPU。`device` 是启动级配置；`/config/reload` 发现其变化时拒绝重载，并提示必须重启服务。

聚合响应中的 `effective_params.device` 继续保留，值来自统一 YOLO 配置。

## 配置与健康检查

新增模型保护配置，开发默认关闭：

```toml
[model_protection]
enabled = false
encrypted_model_root = "/run/screen-det/models-encrypted"
key_file = "/run/screen-det/models-encrypted/model.key"
decrypted_temp_root = "/dev/shm/screen-det-models"
cleanup_after_load = true
```

生产配置必须将 `enabled` 设为 `true`。`/config` 可返回模型保护是否启用、加密目录和临时目录，但不得返回密钥内容。`/health` 返回统一配置设备、两个模型的 loaded/warmed_up 状态以及整体 ready 状态。

## 最终镜像约束

多阶段构建的最终运行层允许包含：

- Python 运行时和第三方运行依赖。
- 编译后的项目 `.so`。
- 空包初始化文件和极薄启动入口。
- 容器运行必需的启动脚本。

最终运行层不得包含：

- `Dockerfile`、requirements 文件、Cython 构建脚本、模型加密脚本和部署验收脚本。
- 项目业务源码、`.pyx`、生成的 `.c`、编译器和构建目录。
- 明文 `.pt`、加密 `.enc` 或 `model.key`。
- PyArmor runtime 或 PyArmor 构建产物。

## 测试与验收

- 单元测试覆盖 AES-GCM 加解密、错误密钥、损坏密文、缺失密钥和临时文件清理。
- 单元测试覆盖明文开发模式与加密生产模式的模型路径解析。
- 单元测试覆盖统一设备配置，并确认旧三个设备字段已删除。
- 单元测试覆盖 `cuda:0` 不可用时启动失败。
- 单元测试覆盖 `/config/reload` 不释放模型、不允许修改启动级配置。
- 使用加密的两个真实模型构建生产镜像并启动，验证两个模型真实推理成功。
- 服务 ready 后删除宿主机模型挂载内容，再次调用屏幕、遮挡和聚合接口，确认继续成功。
- 审计运行容器文件系统，确认不存在业务源码、模型文件、密钥和构建材料。
- 完整运行现有 `screen_det` 单元测试，确保开发模式不受影响。

## 不在本次范围

- 防止宿主机 root 或容器特权用户转储 CPU/GPU 内存。
- 使用 KMS、HSM、远程密钥服务或在线许可证服务器。
- 对第三方 Python 包进行重新编译或隐藏。
- 允许运行中热切换 YOLO 设备或重新加载已删除的模型材料。

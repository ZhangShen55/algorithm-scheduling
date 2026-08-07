## Context

`harden-ai-quality-tias-docker-delivery` 已经为 TIAS 增加了 Cython 编译保护、模型静态加密、CPU/GPU compose 示例和运行文档。但实际在 128 服务器用加密模型启动后，生产容器内仍能看到大量非运行文件和明文模型目录，原因包括：

- 当前 `tias/docker/Dockerfile` 在构建阶段使用 `COPY ./tias ./tias`，最终镜像也保留了这份目录结构。
- 保护构建只删除被编译核心模块的明文 `.py`，没有建立 runtime 镜像文件白名单。
- GPU compose 同时挂载 `../models` 和 `../models-encrypted`，加密模型模式下仍暴露明文 `.pt`。
- 密钥以只读文件挂载到 `/run/secrets/tias_model_key`，容器 root 可以直接读取。

本变更在不接 KMS/Vault 的前提下，继续沿用“本地密钥文件 + 加密模型 + Cython 编译”的路线，目标是把生产镜像和生产 compose 的泄露面降到当前阶段可控的最小范围。

## Goals / Non-Goals

**Goals:**

- 增加 TIAS 生产最小 runtime 镜像，多阶段构建，最终镜像不再整包复制项目。
- runtime 镜像只保留运行必需文件、编译产物和第三方运行依赖。
- 生产 secure compose 不挂载明文模型目录，只挂加密模型目录和必要非模型资产。
- 增加启动期密钥引导：源密钥只读挂载，复制到 `/dev/shm`，应用读取后删除 `/dev/shm` 副本。
- 保留 `docker restart` 能力，只要宿主机源密钥文件和挂载关系仍存在即可重启。
- 增加镜像内容泄露检查，构建或测试阶段发现敏感文件时失败。
- 文档明确普通 compose 与 secure compose 的适用场景、安全边界和 restart 条件。
- 提供 Mac 本地 build 和镜像内容检查命令，但 Codex 不主动执行镜像构建；compose 静态校验、单元测试和 OpenSpec 校验由 Codex 执行；GPU 加密模型实机启动由用户在 128 服务器验证。

**Non-Goals:**

- 不引入 KMS、Vault、HSM、可信执行环境或远程密钥服务。
- 不承诺防止容器 root、宿主机 root、Docker socket 持有者读取挂载源、进程内存或运行时数据。
- 不彻底移除 DirectMHP vendor 代码，也不把 vendor 默认纳入 Cython 编译。
- 不改变 ai_quality 调度、Kafka 消费、数据库表口径、课堂指标算法。
- 不在 Mac 上跑 GPU 推理或 4 节课全量回归。

## Decisions

### 决策 1：新增生产 runtime Dockerfile，保留现有 Dockerfile 作为开发/兼容构建

新增 `tias/docker/Dockerfile.runtime`，采用多阶段构建：

1. `builder` 阶段复制完整 `tias` 源码和构建脚本，安装 Cython 构建依赖，编译 `tias/api`、`tias/core`、`tias/services`、`tias/schemas`。
2. `runtime` 阶段从 builder 复制白名单文件，不再 `COPY ./tias ./tias`。

runtime 白名单：

- `tias/main.py`
- 必要 `__init__.py`
- `tias/api`、`tias/core`、`tias/services`、`tias/schemas` 中的 `.so` 编译产物和必要包初始化文件
- `tias/vendor/DirectMHP` 运行目录
- `tias/start.sh` 或新的 secure entrypoint
- 必要配置运行目录，例如 `/workspace/tias/model-assets`

runtime 禁止保留：

- `Dockerfile*`
- `RUNNING.md`
- `docker/`
- `requirements*.txt`
- `config.toml.example`
- `tests`、`tests2`、`docs`、`openspec`、`tmp`、`mnt`
- `*.pt`、`*.pth`、`*.onnx`、`*.engine`
- `*.key`、`tias_model_key`
- 被编译核心模块对应明文 `.py`

备选方案：

- 继续使用现有 Dockerfile 并在末尾 `rm -rf`：实现快，但容易漏删，镜像历史层也不够直观。
- 只依赖 `.dockerignore`：能缩小 build context，但不能解决 runtime 阶段复制过宽和挂载过宽。
- 新增 runtime Dockerfile：文件更清晰，生产和开发边界明确，当前选择该方案。

### 决策 2：生产 secure compose 独立成文件

新增 `tias/docker/docker-compose.gpu.secure.yml`，与现有 `docker-compose.gpu.yml` 分开：

- 使用 `tias:6.0-secure` 或等效 secure runtime 镜像。
- 使用外部网络 `ai-quality-net`，避免 compose 自动创建隔离网络后无法解析 `ai-quality-api`。
- 给服务配置 aliases：`tias-8981`、`tias-8982`，保证 `BaseUrl` 与容器 DNS 一致。
- 配置 `shm_size: "2g"` 或更大，支持模型临时解密到 `/dev/shm`。
- 不挂载 `../models:/workspace/tias/models:ro`。
- 挂载 `../models-encrypted:/workspace/tias/models-encrypted:ro`。
- 单独挂载非模型资产，例如 `../models/cmu_panoptic_coco.yaml:/workspace/tias/model-assets/cmu_panoptic_coco.yaml:ro`。
- 密钥源文件挂载到 `/run/bootstrap-secrets/tias_model_key:ro`。

备选方案：

- 修改现有 GPU compose：可能破坏开发调试和明文模型兼容路径。
- 单独 secure compose：使用意图明确，便于文档区分开发/生产，当前选择该方案。

### 决策 3：密钥引导采用“只读源文件 + /dev/shm 副本 + 读取后删除副本”

生产容器启动流程：

1. 宿主机密钥文件只读挂载到 `/run/bootstrap-secrets/tias_model_key`。
2. secure entrypoint 启动时校验源文件存在且非空。
3. entrypoint 复制源密钥到 `/dev/shm/tias_model_key`，设置权限 `0400` 或当前运行用户可读。
4. 设置环境变量或配置项使应用读取 `/dev/shm/tias_model_key`。
5. `ModelPathResolver` 第一次读取密钥后清理 `/dev/shm/tias_model_key` 副本。
6. 模型解密到 `/dev/shm/tias-models`，加载成功后按 `CleanupAfterLoad=true` 清理临时明文模型。

restart 语义：

- 只要宿主机源密钥文件仍存在，且容器挂载关系未变，`docker restart` 会重新执行 entrypoint 并重新复制 `/dev/shm` 副本。
- 如果部署后删除宿主机源密钥文件，当前进程可能继续运行，但 `docker restart`、容器重建、宿主机重启、服务崩溃恢复都会失败。

备选方案：

- 直接使用 `/run/secrets/tias_model_key`：简单，但容器内密钥文件长期暴露。
- 启动后删除宿主机源文件：看似更安全，但生产不可恢复，不适合作为默认方案。
- KMS/Vault：更合理，但用户明确本阶段不做。

### 决策 4：镜像泄露检查作为显式验收

新增脚本，例如 `scripts/check_tias_runtime_image.py` 或 shell 脚本，检查镜像内文件：

- 不存在明文模型扩展名。
- 不存在 key 文件或 `tias_model_key`。
- 不存在 `tias/docker`、`docs`、`openspec`、`tests` 等目录。
- 不存在 `Dockerfile*`、`RUNNING.md`、`requirements*.txt` 等非运行文件。
- 核心包目录内不存在非白名单 `.py`。
- 存在必要 `.so`、`main.py`、vendor、entrypoint 和健康检查依赖。

该检查应能在 Mac 上运行，不依赖 GPU。检查失败时返回非 0。

### 决策 5：运行用户与权限作为加固项，但不把 root 权限当安全边界

secure runtime 镜像应尽量使用非 root 用户运行 TIAS，并确保：

- `/dev/shm/tias_model_key` 当前运行用户可读。
- `/dev/shm/tias-models` 权限为 `0700`。
- 配置、加密模型、非模型资产只读挂载。

但文档必须明确：如果用户拥有宿主机 root、Docker socket 或容器 root 权限，本阶段不能阻止其读取挂载源、内存或运行时文件。

## Risks / Trade-offs

- 多阶段 runtime 镜像漏拷运行依赖 → 用容器启动、健康检查和 128 实机 GPU 验证补齐白名单。
- DirectMHP vendor 依赖动态路径 → vendor 保持明文运行目录，不默认 Cython 编译。
- 不挂载明文 `models` 后缺少 `cmu_panoptic_coco.yaml` → 单独挂载到 `model-assets` 并更新 `DirectMHPData` 示例。
- `/dev/shm` 空间不足 → secure compose 默认配置 `shm_size`，文档说明按模型大小调整到 `2g/4g`。
- 启动后删除宿主机密钥导致不可恢复 → 文档和任务明确禁止把删除源密钥作为生产保护手段。
- Mac 本地无法验证 GPU 推理 → Codex 只做静态/单元/OpenSpec 校验，build 和镜像泄露检查命令交给用户执行，128 服务器做 GPU 加密模型启动和推理验证。
- 非 root 运行可能遇到模型缓存、Ultralytics 配置目录不可写 → 设置 `YOLO_CONFIG_DIR=/tmp/Ultralytics` 或等效可写路径。

## Migration Plan

1. 新增 secure runtime Dockerfile 和 secure entrypoint。
2. 新增或调整模型保护读取逻辑，支持读取后删除 `/dev/shm` 密钥副本。
3. 新增 secure GPU compose 和 secure 示例配置。
4. 新增镜像泄露检查脚本。
5. 更新 TIAS Docker 文档和运行文档。
6. 在 Mac 上执行或提供执行命令：
   - secure 镜像 build，由用户执行
   - compose config
   - 镜像泄露检查，由用户在 build 后执行
   - Python 单元测试
   - OpenSpec strict validate
7. 用户在 128 服务器执行：
   - 创建 `ai-quality-net`
   - 构建/拉起 secure GPU compose
   - 验证加密模型启动
   - 验证 TIAS 注册到 ai_quality
   - 验证 `docker restart` 在宿主机密钥源文件存在时可恢复
   - 验证删除宿主机密钥源文件后 restart 失败，并将其作为安全边界说明而非生产流程

回滚策略：

- 保留现有 `tias/docker/Dockerfile` 和 `docker-compose.gpu.yml` 作为开发/兼容路径。
- secure runtime 镜像异常时，先回退到普通 protected 镜像继续服务，再修复 runtime 白名单。

## Open Questions

- secure runtime 镜像最终是否必须非 root 运行；如果现有依赖有权限问题，可第一版先保留 root，但文档明确 root 不是安全边界。
- `/dev/shm` 默认值采用 `2g` 还是 `4g`；建议第一版使用 `2g`，128 服务器实测后再调整。
- DirectMHP 是否只需要 `cmu_panoptic_coco.yaml` 作为非模型资产；如还有其他非模型文件，应在 128 实测中补充白名单。

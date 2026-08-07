## ADDED Requirements

### Requirement: TIAS 必须提供生产最小运行镜像
TIAS MUST 提供生产 secure runtime 镜像，使最终运行镜像只包含服务启动和推理所需文件，不得整包保留项目源码和部署材料。

#### Scenario: runtime 镜像不整包复制 TIAS 项目
- **WHEN** 构建 TIAS 生产 secure runtime 镜像
- **THEN** 最终运行阶段不得通过 `COPY ./tias ./tias` 或等效方式把完整 `tias` 项目目录复制进 runtime 镜像

#### Scenario: runtime 镜像只保留运行白名单
- **WHEN** 查看 TIAS 生产 secure runtime 镜像内容
- **THEN** 镜像内 MUST 只保留运行入口、必要包初始化文件、Cython `.so` 编译产物、DirectMHP vendor 运行依赖、启动脚本和必要运行资产目录

#### Scenario: runtime 镜像排除非运行文件
- **WHEN** 查看 TIAS 生产 secure runtime 镜像内容
- **THEN** 镜像内 MUST NOT 包含 `Dockerfile*`、`RUNNING.md`、`docker/`、`requirements*.txt`、`config.toml.example`、`tests`、`tests2`、`docs`、`openspec`、`tmp` 或 `mnt`

### Requirement: TIAS 生产镜像必须排除明文模型和密钥
TIAS secure runtime 镜像 MUST 不包含明文模型文件和密钥文件。

#### Scenario: 明文模型不进入 runtime 镜像
- **WHEN** 构建 TIAS 生产 secure runtime 镜像
- **THEN** 镜像内 MUST NOT 存在 `*.pt`、`*.pth`、`*.onnx` 或 `*.engine` 文件

#### Scenario: 密钥不进入 runtime 镜像
- **WHEN** 构建 TIAS 生产 secure runtime 镜像
- **THEN** 镜像内 MUST NOT 存在 `*.key`、`*.pem`、`*.crt` 或名为 `tias_model_key` 的文件

#### Scenario: 加密模型通过运行时挂载提供
- **WHEN** TIAS 生产 secure runtime 容器启动
- **THEN** 加密模型目录 MUST 通过只读挂载提供，不得作为明文模型烘焙进镜像层

### Requirement: TIAS 核心模块源码必须在 secure runtime 镜像中隐藏
TIAS secure runtime 镜像 MUST 使用 Cython 编译产物运行核心自研模块，并移除对应明文源码。

#### Scenario: 核心模块存在编译产物
- **WHEN** 查看 TIAS 生产 secure runtime 镜像
- **THEN** `tias/api`、`tias/core`、`tias/services`、`tias/schemas` 中被保护模块 MUST 存在 `.so` 编译产物

#### Scenario: 核心模块明文源码被移除
- **WHEN** 查看 TIAS 生产 secure runtime 镜像
- **THEN** `tias/api`、`tias/core`、`tias/services`、`tias/schemas` 中被编译保护的模块 MUST NOT 保留对应 `.py` 明文源码

#### Scenario: 薄入口和初始化文件可保留
- **WHEN** runtime 需要 Python 入口或包初始化文件
- **THEN** `tias/main.py` 和必要 `__init__.py` MAY 保留，但 MUST 只承担启动、路由装配或包初始化职责

### Requirement: TIAS 必须提供 secure GPU compose 示例
TIAS MUST 提供生产 secure GPU compose 示例，用于加密模型、外部网络、GPU 和共享内存配置。

#### Scenario: secure GPU compose 使用外部 ai_quality 网络
- **WHEN** 查看 TIAS secure GPU compose
- **THEN** compose MUST 使用外部 Docker 网络 `ai-quality-net`，使 TIAS、ai_quality API、ai_quality Worker 和 Redis 可通过容器名互相访问

#### Scenario: secure GPU compose 提供 TIAS 网络别名
- **WHEN** 查看 TIAS secure GPU compose
- **THEN** 每个 TIAS 实例 MUST 配置与 `BaseUrl` 匹配的网络别名，例如 `tias-8981` 和 `tias-8982`

#### Scenario: secure GPU compose 不挂载明文模型目录
- **WHEN** 查看 TIAS secure GPU compose
- **THEN** compose MUST NOT 挂载整个 `../models` 明文模型目录到容器

#### Scenario: secure GPU compose 挂载加密模型和必要非模型资产
- **WHEN** TIAS 使用 secure GPU compose 启动
- **THEN** compose MUST 只读挂载 `models-encrypted`，并按需单独只读挂载 `cmu_panoptic_coco.yaml` 等非模型资产

#### Scenario: secure GPU compose 配置共享内存
- **WHEN** 查看 TIAS secure GPU compose
- **THEN** compose MUST 配置足够的 `shm_size`，用于加密模型临时解密到 `/dev/shm`

### Requirement: TIAS 必须提供镜像泄露检查
TIAS MUST 提供可在 Mac 本地执行的 secure runtime 镜像内容检查能力。

#### Scenario: 检查发现敏感文件时失败
- **WHEN** 镜像内容检查发现明文模型、密钥、非运行目录或核心明文源码
- **THEN** 检查命令 MUST 返回非 0，并输出明确的失败文件或规则

#### Scenario: 检查通过时输出摘要
- **WHEN** 镜像内容检查通过
- **THEN** 检查命令 MUST 输出编译产物数量、被检查的禁止规则和通过结论

#### Scenario: 检查不依赖 GPU
- **WHEN** 在 Mac 本地执行镜像内容检查
- **THEN** 检查 MUST 不依赖 NVIDIA GPU 或 CUDA 运行时可用

## Context

`ai_quality` 已经具备 FastAPI 控制面、Kafka Worker 执行面、Redis 注册表和远程 TIAS 小批次调度能力。`tias` 已经拆为独立推理服务，负责学生/教师图像推理，并向 `ai_quality` 注册实例状态。

当前交付层还存在几个问题：

- `ai_quality` Dockerfile 通过复制 `tias/core` 复用配置加载逻辑，导致两个独立服务在镜像边界上仍然耦合。
- Docker 文档缺少可直接照着部署的 config、NFS、Redis、Nginx、模型目录和密钥挂载说明。
- `tias` Docker compose 目前只有通用示例，没有单独的 GPU 版 compose。
- 镜像内源码和模型文件如果明文交付，容易被直接复制或查看。
- 当前模型保护只能依赖挂载目录权限，无法防止镜像或模型目录被直接拿走后离线使用。

本设计面向 6.0 交付，目标不是实现绝对防逆向，而是提升 Docker 交付的工程边界、安全门槛和可运维性。

## Goals / Non-Goals

**Goals:**

- `ai_quality` Docker 镜像成为独立服务镜像，不再复制 `tias/core`。
- `ai_quality` 支持 Cython 编译构建模式，并在受保护镜像内移除被编译业务模块明文源码。
- `tias` 支持核心自研模块 Cython 编译构建模式，保护 `api`、`core`、`services`、`schemas` 等模块。
- `tias` 支持加密模型交付，运行时通过外部密钥解密到临时目录并加载。
- `ai_quality/docker/` 和 `tias/docker/` 提供详细部署文档和可执行示例。
- `tias/docker/` 提供 GPU 版 docker compose。
- 生产运行文档明确 config.toml、NFS、Redis、Nginx、模型目录、模型密钥的挂载方式。
- 最终回归使用 4 节课任务验证 Kafka、Redis、ai_quality、TIAS、数据库和快照链路。

**Non-Goals:**

- 不承诺 Python 编译或模型加密可以完全防止高权限运行时逆向。
- 不在本阶段引入 KMS、HSM、可信执行环境或专用模型服务。
- 不改课堂质量指标算法、快照保存策略和数据库表口径。
- 不重写 `tias/vendor/DirectMHP`，不默认编译第三方 vendor 代码。
- 不强制生产必须使用 GPU 部署；GPU compose 作为可选部署示例。
- 不把模型密钥写入 Git、Dockerfile、镜像层或 `config.toml`。

## Decisions

### 决策 1：ai_quality 去除对 tias.core 的镜像依赖

`ai_quality` 必须拥有自己的配置加载逻辑或轻量通用配置工具，Dockerfile 不再复制 `tias/core` 和 `tias/__init__.py`。这是服务拆分后的边界要求。

备选方案：

- 继续复制 `tias/core`：改动少，但服务边界不清晰，后续独立仓库或独立镜像发布会持续受影响。
- 抽一个公共包：架构更干净，但当前只有配置加载一个小依赖，引入公共包会增加发布复杂度。
- 在 `ai_quality` 内实现配置加载：最直接，当前选择该方案。

### 决策 2：Cython 编译作为交付保护层，不作为强安全边界

`ai_quality` 使用 Cython 编译主要业务模块。`tias` 编译核心自研模块。编译后镜像内应删除对应 `.py` 源文件，仅保留必要入口文件、配置模板、静态资源和编译产物。

需要保留明文的文件包括：

- 包初始化需要的 `__init__.py`，如可替换为空文件则替换为空文件。
- CLI 或 ASGI 入口文件，如必须保留，应尽量保持薄入口。
- 第三方 vendor、配置模板、运行文档不纳入编译保护范围。

备选方案：

- 只生成 `.pyc`：成本低，但保护强度很弱，容易反编译。
- PyArmor：落地快，但涉及授权、运行时兼容和长期维护。
- Nuitka 全量编译：保护更强，但 PyTorch、OpenCV、动态导入和镜像体积风险较高。
- Cython 分模块编译：保护强度和工程风险较均衡，当前选择该方案。

### 决策 3：TIAS 只编译核心自研模块

`tias` 编译范围限定为：

- `tias/api`
- `tias/core`
- `tias/services`
- `tias/schemas`
- 必要时包含 `tias/main.py` 的薄入口适配

不默认编译：

- `tias/vendor/DirectMHP`
- `tias/models`
- 测试目录
- Docker、文档、OpenSpec 目录

理由是 vendor 代码包含动态导入、相对路径和第三方实现，编译风险高；模型目录属于资产保护问题，不属于 Cython 保护范围。

### 决策 4：模型加密采用“静态加密 + 运行时临时解密加载”

模型以 `.enc` 文件交付，明文 `.pt` 不进入受保护镜像，不进入 Git。TIAS 启动时读取运行时注入的密钥，将模型解密到临时目录，例如 `/dev/shm/tias-models`，加载完成后删除临时明文文件。

推荐配置：

```toml
[ModelProtection]
Enabled = true
EncryptedModelRoot = "/workspace/tias/models-encrypted"
DecryptedTempRoot = "/dev/shm/tias-models"
KeyFile = "/run/secrets/tias_model_key"
CleanupAfterLoad = true
```

使用方式：

- 构建或发布前运行加密脚本，把明文模型转成 `.enc`。
- 运行容器时通过 Docker secret 或只读 bind mount 注入密钥文件。
- 模型加载器在 `Enabled=true` 时解析加密模型路径，解密后把临时明文路径交给 `ultralytics.YOLO` 或 DirectMHP `attempt_load`。

备选方案：

- 纯内存解密：理论上磁盘残留更少，但当前 YOLO 和 DirectMHP 多数接口按路径加载，改造风险较大。
- 模型服务化：安全边界更强，但需要额外模型服务和调用协议，不适合本阶段。
- 只靠目录权限：成本低，但静态文件被复制后没有保护。

### 决策 5：密钥只允许运行时注入

模型密钥不得出现在：

- Git 仓库。
- Dockerfile。
- Docker build arg。
- 镜像环境变量。
- `config.toml`。
- OpenSpec 文档示例真实值。

允许方式：

- Docker secret：`/run/secrets/tias_model_key`。
- 宿主机只读挂载：`-v /secure/tias_model_key:/run/secrets/tias_model_key:ro`。
- 临时环境变量仅用于本地测试，但生产不推荐。

### 决策 6：Docker 文档必须以“可运行命令”为准

`ai_quality` 文档必须覆盖：

- Redis Docker 部署。
- NFS 挂载到宿主机项目 `mnt`。
- `config.toml` 挂载到容器。
- API 单实例部署。
- Worker 独立部署。
- 2 个 API 实例 + Nginx 统一入口。
- Worker 控制接口调用。
- 日志查看、健康检查、停止和清理。

`tias` 文档必须覆盖：

- CPU 单实例部署。
- GPU 单实例部署。
- 多实例部署。
- 模型明文挂载模式。
- 模型加密挂载模式。
- 注册到 `ai_quality` 的配置。
- Docker compose CPU/GPU 示例。
- 常见问题：模型文件被删除、GPU 不可见、注册不上、配置路径错误。

### 决策 7：GPU compose 独立成文件

`tias/docker/docker-compose.gpu.yml` 独立于通用 compose，避免 CPU 环境执行 compose 时被 GPU 配置阻塞。GPU compose 必须包含 NVIDIA 设备声明，并在文档中说明宿主机依赖。

示例结构：

```text
tias/docker/
├── docker-compose.yml
├── docker-compose.gpu.yml
├── examples/
│   ├── tias-8981.toml
│   └── tias-8982.toml
└── README.md
```

### 决策 8：Docker build 默认保守，保护构建显式开启

默认 Dockerfile 可以保留开发友好的构建方式；受保护构建通过 build arg 或专用 Dockerfile 显式开启：

```bash
docker build -f ai_quality/docker/Dockerfile \
  --build-arg PROTECT_SOURCE=1 \
  -t ai-quality:6.0-protected .
```

如果实际实现中 Cython 多阶段构建复杂，也可以拆为：

- `Dockerfile`
- `Dockerfile.protected`

选择标准是可维护性优先，不为减少一个文件牺牲可读性。

### 决策 9：最终回归测试固定为 4 节课

回归任务使用 4 条 Kafka 消息，topic 固定 `classroom_cv_task`。验证内容包括：

- Redis 可用。
- `ai_quality-api` 可查询健康状态、Worker 状态和 TIAS 注册表。
- `ai_quality-worker` 可受控 resume/pause/drain。
- 至少 2 个 TIAS 实例参与调度；如本机资源允许，优先 4 个 TIAS 实例。
- 4 条任务全部写入成功终态。
- 每条任务有行为时间线、核心快照、学生行为统计和指标得分结果。
- 快照写入挂载目录，数据库 `image_url` 保存相对路径。
- Kafka offset 按成功或最终失败口径提交。

## Risks / Trade-offs

- Cython 编译导致动态导入失败 → 先编译核心模块，保留薄入口和必要 `__init__.py`，用容器启动和 4 节课回归验证。
- Cython 编译导致排查问题变难 → 保留未保护开发镜像，生产使用 protected 镜像。
- 模型解密临时文件被高权限用户读取 → 使用 `/dev/shm`、`0700` 权限、加载后删除，并在文档中说明无法防高权限运行时逆向。
- 密钥被错误写进镜像或配置 → `.dockerignore`、文档和测试检查禁止常见密钥文件进入镜像上下文。
- GPU compose 在不同 Docker Compose 版本字段兼容性不同 → 文档给出 `docker compose config` 校验步骤，并说明 NVIDIA Container Toolkit 前置要求。
- 模型挂载目录被宿主机删除 → 文档明确当前进程可能继续运行但重启必然失败，不允许把“删除后仍可运行”作为生产假设。
- 4 节课回归耗时仍较长 → 保留单元测试、compose 静态校验和容器冒烟测试作为快速反馈，4 节课回归作为最终验收。

## Migration Plan

1. 提交 OpenSpec 方案并评审。
2. 实现 `ai_quality` 独立配置加载和独立依赖清单。
3. 增加 Cython 编译构建脚本和 Docker protected 构建。
4. 增加 `.dockerignore`，排除 `.git`、测试数据、明文模型、密钥、临时产物。
5. 实现 TIAS 模型加密/解密加载工具和配置项。
6. 补充 `ai_quality`、`tias` Docker README 和运行文档。
7. 增加 TIAS GPU compose。
8. 运行单元测试、OpenSpec 校验、Docker compose 静态校验、容器冒烟测试。
9. 使用 4 条 Kafka 课堂任务做端到端回归，形成运行报告。

回滚策略：

- 保留普通未保护 Docker 构建路径。
- `ModelProtection.Enabled=false` 时继续支持明文模型路径。
- Cython protected 镜像出现兼容问题时，可回退到普通镜像并保留部署文档和挂载修正。

## Open Questions

- 生产环境最终使用 Docker secret 还是宿主机只读文件挂载注入模型密钥。
- `tias` GPU 版基础镜像是否继续使用 `pytorch/pytorch:2.6.0-cuda11.8-cudnn9-runtime`，还是切换为当前环境更容易拉取的内部镜像。
- 模型加密算法是否指定 AES-GCM；本设计建议 AES-GCM，但最终可根据依赖兼容性调整。

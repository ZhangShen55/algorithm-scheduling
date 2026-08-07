## Why

`ai_quality` 和 `tias` 已经拆成两个独立服务，但当前容器交付仍存在服务边界不清、部署文档不够完整、镜像可能包含明文源码和模型资产的问题。为了进入 6.0 版本交付，需要补齐 Docker 安全构建、代码编译保护、模型静态加密、NFS/config 挂载和多实例部署说明，并用 4 节课端到端回归验证交付链路。

## What Changes

- `ai_quality` Docker 镜像去除对 `tias.core` 的运行依赖，改为自身配置加载逻辑和独立依赖清单。
- `ai_quality` 增加 Cython 编译构建模式，镜像内不保留被编译业务模块的明文 `.py` 源码。
- `tias` 增加核心自研模块 Cython 编译构建模式，优先保护 `api`、`core`、`services`、`schemas` 等模块，不编译第三方 `vendor/DirectMHP`。
- `tias` 增加模型静态加密交付能力：镜像或挂载目录保存 `.enc` 模型，运行时用外部注入密钥解密到临时目录后加载，密钥不得写入镜像或配置文件。
- `ai_quality/docker/` 和 `tias/docker/` 增加 `.dockerignore`、构建脚本、部署示例和详细 README，说明 config.toml、NFS、Redis、Nginx、模型目录、密钥文件的挂载方式。
- `tias/docker/` 增加 GPU 版 docker compose 示例，说明 NVIDIA Container Toolkit、GPU 资源声明和多实例配置。
- `ai_quality` 部署文档补充 2 个 API 实例通过 Nginx 统一入口访问的方案，明确 Nginx 只代理 API，不代理 Worker。
- 最终回归测试从 6 节课调整为 4 节课，仍覆盖 Kafka、Redis、ai_quality API/Worker、多 TIAS 调度、数据库写入、快照写入和 Docker 构建/启动验证。

## Capabilities

### New Capabilities

- `ai-quality-secure-delivery`: 定义 `ai_quality` 独立 Docker 交付、Cython 编译保护、配置/NFS/Redis/Nginx 部署文档和 4 节课回归要求。
- `tias-secure-delivery`: 定义 `tias` Docker 交付、核心模块编译保护、CPU/GPU compose、多实例部署和运行文档要求。
- `model-artifact-protection`: 定义模型文件静态加密、运行时密钥注入、临时解密加载、明文清理和安全边界说明。

### Modified Capabilities

- 无。当前仓库主规格目录为空，本变更以新增能力形式定义交付要求。

## Impact

- 影响代码：
  - `ai_quality/config.py`、`ai_quality/docker/`、`ai_quality/RUNNING.md`、`ai_quality/requirements*` 或新增构建脚本。
  - `tias/core`、`tias/services`、`tias/api`、`tias/schemas`、`tias/docker/`、`tias/RUNNING.md`、`tias/models` 加载路径相关逻辑。
- 影响部署：
  - Docker build 需支持普通构建和编译保护构建。
  - 生产运行需挂载 `config.toml`、NFS 快照目录、模型目录或加密模型目录、模型密钥 secret。
  - GPU 版 TIAS 宿主机需安装 NVIDIA Container Toolkit。
- 影响依赖：
  - 新增 Cython 编译依赖。
  - 新增模型加解密依赖，优先使用 `cryptography` 或标准化 AES-GCM 实现。
- 影响测试：
  - 增加 Docker 静态校验、Cython 编译产物校验、模型加密加载校验。
  - 端到端全量回归使用 4 条 Kafka 课堂任务，不再跑 6 条。

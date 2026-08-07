## Why

当前 TIAS 保护镜像仍通过 `COPY ./tias ./tias` 将大量非运行文件带入镜像，生产容器中还能看到明文模型、部署文档、Docker 文件、配置模板和密钥挂载文件，交付泄露面过大。需要在不引入 KMS/Vault 的前提下，先完成生产最小运行镜像、加密模型挂载和启动期密钥副本清理，降低静态交付和运行期文件暴露风险。

## What Changes

- 新增 TIAS 生产最小运行镜像构建方式，使用多阶段 Docker build，runtime 阶段只保留运行必需文件和编译产物，不再整包复制 `tias` 项目。
- 生产 runtime 镜像中不得包含明文模型、密钥、Docker/部署文档、测试目录、OpenSpec、临时目录、非必要 requirements 和被编译核心模块明文源码。
- 新增生产 secure GPU compose 示例，使用外部 `ai-quality-net` 网络、TIAS 网络别名、GPU 资源声明、`shm_size`、加密模型目录挂载和最小必要非模型资产挂载。
- 加密模型模式下不再挂载整个 `tias/models` 明文模型目录；如 DirectMHP 需要 `cmu_panoptic_coco.yaml`，只单独挂载该非模型配置文件。
- 增加密钥引导机制：宿主机密钥源文件只读挂载到 `/run/bootstrap-secrets/tias_model_key`，entrypoint 启动时复制到 `/dev/shm/tias_model_key`，应用读取 `/dev/shm/tias_model_key`，读取后删除 `/dev/shm` 副本。
- 保留 `docker restart` 能力：宿主机密钥源文件不删除、不移除挂载时，每次重启都重新生成 `/dev/shm` 密钥副本。
- 增加镜像泄露检查脚本或构建校验，发现明文模型、密钥、非运行目录或核心明文源码时失败。
- 更新 TIAS Docker 文档，区分开发 compose 和生产 secure compose，明确本阶段安全边界：不使用 KMS/Vault，不承诺防止容器 root 或宿主机 root 读取挂载源、内存或运行期数据。
- 本阶段提供 Docker build 和镜像内容检查命令；Codex 不在本机执行镜像构建。compose config、单元测试和 OpenSpec 校验由 Codex 执行，GPU 加密模型实机启动由用户在 128 服务器验证。

## Capabilities

### New Capabilities

- `tias-secure-runtime-image`: 定义 TIAS 生产最小运行镜像、多阶段构建、运行文件白名单和镜像泄露检查要求。
- `tias-runtime-key-bootstrap`: 定义本地密钥文件方式下的启动期密钥复制、`/dev/shm` 副本读取清理、restart 语义和安全边界。

### Modified Capabilities

- 无。当前主规格目录未包含已归档能力，本变更以新增能力形式约束 6.0 后续交付加固。

## Impact

- 影响代码：
  - `tias/docker/Dockerfile` 或新增 `tias/docker/Dockerfile.runtime`
  - `tias/docker/docker-compose.gpu.yml` 或新增 `tias/docker/docker-compose.gpu.secure.yml`
  - `tias/docker/entrypoint` 或启动脚本
  - `tias/core/model_protection.py`
  - `tias/docker/README.md`、`tias/RUNNING.md`
  - 新增镜像内容检查脚本
- 影响部署：
  - 生产加密模型部署不再挂载明文模型目录。
  - 生产密钥源文件必须保留在宿主机受控路径，不能在部署完成后删除，否则容器 restart/recreate 后无法解密模型。
  - 生产容器需要足够大的 `/dev/shm`，建议从 `2g` 起按模型大小调整。
- 影响测试：
  - Mac 本地验证以用户执行 build、静态校验和镜像内容检查为主。
  - 128 GPU 服务器验证加密模型启动、TIAS 心跳注册、模型推理和 restart 行为。

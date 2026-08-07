## 1. 生产 runtime 镜像

- [x] 1.1 新增 `tias/docker/Dockerfile.runtime`，使用多阶段构建生成 TIAS secure runtime 镜像，保留现有开发/兼容 Dockerfile 不破坏。
- [x] 1.2 在 builder 阶段完成 TIAS 自研模块 Cython 编译，覆盖 `tias/api`、`tias/core`、`tias/services`、`tias/schemas`。
- [x] 1.3 在 runtime 阶段按白名单复制运行入口、必要 `__init__.py`、`.so` 编译产物、DirectMHP vendor、启动脚本和必要运行资产，不得整包 `COPY ./tias ./tias`。
- [x] 1.4 确认 runtime 镜像中不包含 `Dockerfile*`、`RUNNING.md`、`docker/`、`requirements*.txt`、`config.toml.example`、`tests`、`tests2`、`docs`、`openspec`、`tmp`、`mnt` 等非运行文件。
- [x] 1.5 确认 runtime 镜像中不包含 `*.pt`、`*.pth`、`*.onnx`、`*.engine`、`*.key`、`*.pem`、`*.crt` 或 `tias_model_key`。

## 2. 密钥启动引导与模型保护

- [x] 2.1 新增 secure entrypoint 脚本，从 `/run/bootstrap-secrets/tias_model_key` 校验并复制密钥到 `/dev/shm/tias_model_key`。
- [x] 2.2 entrypoint 复制密钥后设置运行期副本权限为当前运行用户可读，推荐 `0400`，源密钥缺失或为空时启动失败并输出简洁错误。
- [x] 2.3 调整 TIAS 模型保护配置示例，使加密模型模式下 `KeyFile` 指向 `/dev/shm/tias_model_key`，`DecryptedTempRoot` 指向 `/dev/shm/tias-models`。
- [x] 2.4 调整 `tias/core/model_protection.py` 或等效实现，首次读取 `/dev/shm/tias_model_key` 后缓存内存密钥并删除 `/dev/shm` 副本。
- [x] 2.5 删除 `/dev/shm/tias_model_key` 失败时只记录简洁警告，不输出密钥内容；后续模型解密不得再次依赖副本文件存在。
- [x] 2.6 确认 `CleanupAfterLoad=true` 时模型加载成功后清理 `/dev/shm/tias-models` 下的临时明文模型文件，临时目录权限推荐 `0700`。

## 3. secure GPU compose 与配置

- [x] 3.1 新增 `tias/docker/docker-compose.gpu.secure.yml`，使用 secure runtime 镜像、GPU 资源声明、`shm_size` 和外部网络 `ai-quality-net`。
- [x] 3.2 为每个 TIAS 实例配置与 `BaseUrl` 匹配的 Docker 网络别名，例如 `tias-8981`、`tias-8982`。
- [x] 3.3 secure compose 不再挂载整个 `../models` 明文模型目录，只读挂载 `../models-encrypted`。
- [x] 3.4 如 DirectMHP 需要 `cmu_panoptic_coco.yaml`，仅将该非模型资产单文件只读挂载到 runtime 镜像约定目录。
- [x] 3.5 secure compose 将宿主机密钥源文件只读挂载到 `/run/bootstrap-secrets/tias_model_key`，不再长期挂载到应用直接读取路径。
- [x] 3.6 补充 secure 配置样例或 compose 环境变量，确保 TIAS 注册 ai_quality API、模型加密开关、临时解密目录、日志级别和端口等运行参数清晰可配置。

## 4. 镜像内容检查

- [x] 4.1 新增 Mac 可执行的 TIAS secure runtime 镜像内容检查脚本，不依赖 NVIDIA GPU 或 CUDA 运行时。
- [x] 4.2 检查脚本发现明文模型、密钥、非运行目录、非运行文件或核心明文源码时返回非 0，并输出具体失败规则和文件。
- [x] 4.3 检查脚本通过时输出 `.so` 编译产物数量、禁止规则摘要和通过结论。
- [x] 4.4 将镜像内容检查命令写入 TIAS Docker 文档，作为 secure runtime build 后必跑验收项。

## 5. 文档更新

- [x] 5.1 更新 `tias/docker/README.md`，区分开发 compose、普通 GPU compose 和生产 secure GPU compose 的用途。
- [x] 5.2 文档补充 `ai-quality-net` 创建、Redis/ai_quality/TIAS 同网络运行、secure GPU compose 启动命令和常见排错。
- [x] 5.3 文档明确密钥源文件必须保留在宿主机受控路径；删除源密钥会导致 `docker restart`、容器重建、宿主机重启或故障恢复失败。
- [x] 5.4 文档明确本阶段不使用 KMS/Vault，不承诺防止容器 root、宿主机 root 或 Docker socket 持有者读取挂载源、内存或运行时文件。
- [x] 5.5 更新 `tias/RUNNING.md`，补充 secure runtime 启动、注册 ai_quality、加密模型路径、`/dev/shm` 空间和日志核查要点。
- [x] 5.6 文档说明 Mac 本地只做 build、compose config、镜像内容检查和单元测试；128 GPU 服务器由用户执行加密模型实机验证。

## 6. 验证

- [x] 6.1 提供 secure runtime 镜像 build 命令；按用户要求 Codex 不在本机执行镜像构建。
- [x] 6.2 在 Mac 上执行 `docker compose -f tias/docker/docker-compose.gpu.secure.yml config`，确认 compose 语法、网络、挂载和环境变量有效。
- [x] 6.3 提供 TIAS secure runtime 镜像内容检查脚本和命令；按用户要求 Codex 不在本机执行镜像内容检查。
- [x] 6.4 运行与模型保护、密钥读取清理、配置解析相关的 Python 单元测试或最小验证脚本。
- [x] 6.5 执行 `openspec validate harden-tias-secure-runtime-image --strict` 并通过。
- [x] 6.6 给出 128 GPU 服务器验证清单：secure compose 启动、加密模型加载、TIAS 注册、推理接口、源密钥保留时 `docker restart` 成功、源密钥删除后 restart 失败且符合文档边界。

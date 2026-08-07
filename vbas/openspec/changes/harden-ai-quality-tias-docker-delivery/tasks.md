## 1. ai_quality 独立交付基础

- [x] 1.1 梳理 `ai_quality` 对 `tias.*` 的导入，确认远程推理模式下不需要 TIAS 推理实现。
- [x] 1.2 在 `ai_quality` 内实现独立 `config.toml` 加载逻辑，移除对 `tias.core.config_loader` 的依赖。
- [x] 1.3 为 `ai_quality` 补充独立依赖清单，Dockerfile 不再使用 `tias/requirements.txt`。
- [x] 1.4 修改 `ai_quality/docker/Dockerfile`，删除 `COPY ./tias/core` 和 `COPY ./tias/__init__.py`。
- [x] 1.5 验证 `python -m ai_quality.app --config ai_quality/config.toml serve` 和 `worker` 入口仍可启动。

## 2. Cython 编译保护

- [x] 2.1 增加通用 Cython 构建脚本，支持按包路径编译并保留必要薄入口。
- [x] 2.2 为 `ai_quality` 增加保护构建配置，编译业务模块并移除对应明文 `.py` 文件。
- [x] 2.3 为 `tias` 增加保护构建配置，编译 `api`、`core`、`services`、`schemas` 等核心自研模块。
- [x] 2.4 明确排除 `tias/vendor/DirectMHP`、模型目录、测试目录和部署文档，不纳入 Cython 编译。
- [x] 2.5 增加普通构建和保护构建的 Docker build 参数或专用 Dockerfile。
- [x] 2.6 验证保护镜像内被保护模块存在 `.so` 产物，且对应明文源码已移除。

## 3. 模型加密与加载

- [x] 3.1 设计并实现模型加密工具，将明文模型目录转换为 `.enc` 加密模型目录。
- [x] 3.2 设计并实现模型解密加载工具，支持从密钥文件读取密钥并解密到临时目录。
- [x] 3.3 补充 `[ModelProtection]` 配置项，支持启用/关闭模型保护、加密目录、临时目录、密钥文件和加载后清理。
- [x] 3.4 改造 YOLO 模型加载路径，使学生行为、人数、人脸和教师行为模型支持加密模型解密后加载。
- [x] 3.5 改造 DirectMHP 模型加载路径，使教师头部姿态模型支持加密模型解密后加载。
- [x] 3.6 加载成功后按配置删除临时明文模型文件，并记录简洁中文日志。
- [x] 3.7 增加加密解密一致性测试、错误密钥失败测试和最小模型加载冒烟测试。

## 4. Docker 构建和部署文件

- [x] 4.1 增加或更新 `.dockerignore`，排除 `.git`、测试视频、缓存、临时目录、明文模型、密钥和本地配置。
- [x] 4.2 更新 `ai_quality/docker/docker-compose.yml`，确保 API、Worker、Redis、config.toml 和 `/mnt` 挂载清晰。
- [x] 4.3 更新 `ai_quality/docker/nginx.conf.example`，提供 2 个 API 实例 upstream 示例。
- [x] 4.4 更新 `tias/docker/docker-compose.yml`，保持 CPU 或通用多实例示例可静态校验。
- [x] 4.5 新增 `tias/docker/docker-compose.gpu.yml`，包含 NVIDIA GPU 资源声明和多实例示例。
- [x] 4.6 更新 `tias/docker/examples/*.toml`，补充并发、队列、注册 ai_quality、模型保护相关配置和中文注释。
- [x] 4.7 验证 `docker compose -f ai_quality/docker/docker-compose.yml config` 通过。
- [x] 4.8 验证 `docker compose -f tias/docker/docker-compose.yml config` 通过。
- [x] 4.9 验证 `docker compose -f tias/docker/docker-compose.gpu.yml config` 通过。

## 5. 部署和运行文档

- [x] 5.1 补充 `ai_quality/RUNNING.md`，写清 Redis、API、Worker、NFS、config.toml、Nginx 双 API 和控制接口操作。
- [x] 5.2 补充 `ai_quality/docker/README.md`，给出可直接执行的 docker run 和 compose 示例。
- [x] 5.3 补充 `tias/RUNNING.md`，写清 CPU/GPU、多实例、模型明文挂载、模型加密挂载、注册 ai_quality 和健康检查。
- [x] 5.4 补充 `tias/docker/README.md`，给出 CPU compose、GPU compose、docker run 和常见问题说明。
- [x] 5.5 文档中明确宿主机删除模型文件对运行中进程、重启和懒加载的影响。
- [x] 5.6 文档中明确模型加密只保护静态文件，不承诺防止高权限运行时逆向。

## 6. 验证和回归

- [x] 6.1 运行 Python 单元测试，确认 ai_quality 和 TIAS 现有功能未回退。
- [x] 6.2 运行 `openspec validate harden-ai-quality-tias-docker-delivery --strict`。
- [x] 6.3 构建 ai_quality 普通镜像和保护镜像，并完成 API/Worker 容器冒烟验证。
- [x] 6.4 构建 TIAS 普通镜像和保护镜像，并完成最小推理或健康检查冒烟验证。
- [x] 6.5 使用加密模型和运行时密钥启动 TIAS，验证模型可加载且临时明文按配置清理。
- [x] 6.6 本地启动 Redis、ai_quality-api、ai_quality-worker 和至少 2 个 TIAS 实例；资源允许时启动 4 个 TIAS 实例。
- [x] 6.7 向 Kafka topic `classroom_cv_task` 投递 4 条课堂任务并完成端到端处理。
- [x] 6.8 核对 4 条任务的 `lesson_ai_workflow`、`lesson_behavior_timeline`、`lesson_snapshot_event`、`lesson_student_behavior_stat` 和 `indicator_score_result` 写入结果。
- [x] 6.9 核对快照文件写入挂载目录，数据库 `image_url` 为相对路径。
- [x] 6.10 在 `openspec/changes/harden-ai-quality-tias-docker-delivery/reports/` 写入 4 节课回归报告。

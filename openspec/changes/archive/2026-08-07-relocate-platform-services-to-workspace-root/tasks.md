## 1. 建立安全 Git 基线

- [x] 1.1 审计工作区文件大小、模型与媒体扩展名、日志、缓存、虚拟环境、生成目录、环境文件和配置中的潜在秘密，并记录纳入与排除边界
- [x] 1.2 创建根 `.gitignore`，排除模型权重、媒体、日志、缓存、虚拟环境、运行数据、临时文件和秘密文件，同时保留安全默认配置、源码、测试、迁移、文档、OpenSpec 与 Harness
- [x] 1.3 在工作区根初始化 Git 仓库，并将 `origin` 配置为 `git@github.com:ZhangShen55/algorithm-scheduling.git`
- [x] 1.4 分批暂存安全文件，检查暂存文件大小和敏感内容，并形成移动前可恢复基线提交

## 2. 移动四个服务并收敛内部包

- [x] 2.1 使用 Git 感知移动将四个服务目录迁移到工作区根，并确认旧 `algorithm-scheduling-platform/services` 不再包含运行源码
- [x] 2.2 将 `control_service` 根级兼容实现归入本地 `app` 分层，使用包相对导入并删除 `services.control_service` 兼容入口
- [x] 2.3 将 `orchestrator_service` 根级兼容实现归入本地 `app` 分层，使用包相对导入并删除 `services.orchestrator_service` 兼容入口
- [x] 2.4 将 `vision_orchestrator_service` 内部导入收敛到本地 `app` 包，删除 `services.vision_orchestrator_service` 兼容入口
- [x] 2.5 将 `online_gateway_service` 内部导入收敛到本地 `app` 包，删除 `services.online_gateway_service` 兼容入口
- [x] 2.6 为四个服务补充或调整结构测试，验证 `app.main:app`、必需项目文件和禁止旧包前缀规则

## 3. 显式化公共包与独立构建

- [x] 3.1 调整 `algorithm-scheduling-platform/pyproject.toml`，只打包公共 `packages*` 和仍需交付的支撑模块，不再发布 `services*`
- [x] 3.2 为四个服务声明可复现的公共平台分发包安装方式，并在各 README 记录从服务目录进行本地安装、测试和启动的命令
- [x] 3.3 重写 `control_service/docker/Dockerfile`，只安装公共包和复制当前服务，并使用 `app.main:app` 启动
- [x] 3.4 重写 `orchestrator_service/docker/Dockerfile`，保留 FFmpeg 依赖但只安装公共包和复制当前服务，并使用 `app.main:app` 启动
- [x] 3.5 重写 `vision_orchestrator_service/docker/Dockerfile`，只安装公共包和复制当前服务，并使用 `app.main:app` 启动
- [x] 3.6 重写 `online_gateway_service/docker/Dockerfile`，只安装公共包和复制当前服务，并使用 `app.main:app` 启动

## 4. 更新部署、测试与文档路径

- [x] 4.1 更新平台 Compose 中四个构建 Dockerfile、配置挂载和上下文路径，保持服务名、端口、依赖关系和可选部署边界不变
- [x] 4.2 更新平台测试，使单服务测试分别从服务目录运行，跨服务测试通过公共契约、子进程、Compose 或 HTTP 边界验证
- [x] 4.3 更新 Makefile、验证脚本、部署指南、运维手册、平台 README 和服务 README 中的旧目录、导入与启动命令
- [x] 4.4 更新总体设计文档、Harness 证据和当前活动 OpenSpec 变更中的有效旧路径，并保留必要的历史迁移说明
- [x] 4.5 增加旧路径门禁，禁止有效源码和交付配置继续使用 `algorithm-scheduling-platform/services` 或 `services.<service_name>`

## 5. 分层验证迁移

- [x] 5.1 分别在四个服务目录运行 `python -m compileall -q app` 并验证 `from app.main import app`
- [x] 5.2 分别运行四个服务自身测试，确认同名 `app` 包不会跨服务污染
- [x] 5.3 运行平台公共包、契约、目录、部署和非集成测试，并运行 Ruff 与 Mypy 适用检查
- [x] 5.4 解析全部 Compose 定义，确认四个服务的新构建路径、配置挂载和核心/可选部署组合有效
- [x] 5.5 分别构建四个服务镜像，启动并检查健康接口，确认镜像未复制其他服务源码
- [x] 5.6 对比迁移前后的 HTTP/WebSocket 路由、默认端口、配置字段和算子注册协议，确认网络业务契约无变化
- [x] 5.7 执行全工作区旧引用扫描并更新 Harness 验证证据，记录各验证层级的实际结果

## 6. 形成可审查迁移提交

- [x] 6.1 检查最终 Git diff、重命名识别、暂存文件清单、大文件和秘密扫描结果，确认未丢失用户文件或混入运行产物
- [x] 6.2 将目录迁移、导入调整、构建部署更新和验证证据形成独立本地提交
- [x] 6.3 报告本地提交、`origin` 地址和未推送状态，等待用户明确确认后再执行远端推送

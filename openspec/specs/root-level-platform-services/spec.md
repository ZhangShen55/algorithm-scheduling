# 根级平台服务规范

## Purpose

规定四个平台服务在工作区根目录中的独立项目边界、公共依赖、构建部署方式和兼容性要求，确保每个服务可以单独安装、测试、启动和交付，同时保留共享平台契约以及可选部署关系。

## Requirements

### Requirement: 四个服务位于工作区根目录
系统 SHALL 在工作区根目录分别提供 `control_service`、`orchestrator_service`、`vision_orchestrator_service` 和 `online_gateway_service`，且旧 `algorithm-scheduling-platform/services` 不再作为运行或构建来源。

#### Scenario: 根目录布局完成
- **WHEN** 开发者检查工作区的可部署项目
- **THEN** 四个服务目录均直接位于工作区根目录，并且部署配置不再引用旧服务目录

### Requirement: 服务可从自身目录独立运行
每个服务 SHALL 从自身项目目录使用 `app.main:app` 完成导入和 Uvicorn 启动，不得依赖 `services.<service_name>`、符号链接、临时 `PYTHONPATH` 或特定父目录作为当前工作目录。

#### Scenario: 独立导入应用
- **WHEN** 开发者进入任一服务目录并执行 `python -c "from app.main import app"`
- **THEN** 应用对象成功导入且不需要将其他服务目录加入 Python 路径

#### Scenario: 使用标准入口启动
- **WHEN** 开发者在服务目录执行该服务 README 记载的 `python -m uvicorn app.main:app` 命令
- **THEN** 服务在规定端口启动并通过自身健康检查

### Requirement: 服务具有完整独立项目资产
每个服务 SHALL 自有 `app/`、`tests/`、`docker/Dockerfile`、`config.toml`、`requirements.txt` 和 `README.md`；`app` 内部 SHALL 使用不依赖旧父包的包相对导入，并且不得使用 `services.<service_name>`。

#### Scenario: 检查项目结构
- **WHEN** 结构测试检查任一根目录服务
- **THEN** 必需项目资产全部存在，且有效服务代码中不存在 `services.<service_name>` 导入

### Requirement: 公共平台代码是显式依赖
跨服务共享的响应契约、状态枚举、repository、指标和工作区能力 SHALL 由可安装的公共分发包提供；服务不得通过复制公共源码或临时 Python 路径访问这些能力。

#### Scenario: 本地安装公共依赖
- **WHEN** 开发者按服务 README 创建环境并安装依赖
- **THEN** 服务能够正常导入公共平台包并运行测试

#### Scenario: 公共契约仅有一个来源
- **WHEN** 四个服务使用相同的业务状态或响应契约
- **THEN** 它们均从公共分发包导入，而不是在各服务内维护重复实现

### Requirement: Docker 镜像遵守服务边界
每个服务的 Docker 构建 SHALL 只包含当前服务、必要运行依赖和公共平台分发包，不得复制其他三个服务；容器 SHALL 使用 `app.main:app` 启动。

#### Scenario: 独立构建服务镜像
- **WHEN** 运维人员使用文档指定的工作区构建上下文和该服务 Dockerfile 构建镜像
- **THEN** 镜像构建成功，且无需把其他服务源码复制到镜像

#### Scenario: 容器入口符合标准
- **WHEN** 检查任一服务镜像的默认启动命令
- **THEN** 命令使用 `python -m uvicorn app.main:app` 和该服务规定端口

### Requirement: 服务部署组合保持可选边界
部署编排 SHALL 要求 `control_service` 与 `orchestrator_service` 支撑离线处理，并 SHALL 允许在不需要视觉离线分析或在线能力时分别省略 `vision_orchestrator_service` 和 `online_gateway_service`。

#### Scenario: 只部署核心离线能力
- **WHEN** 交付环境只启用不含教师和学生行为的离线任务
- **THEN** `control_service` 与 `orchestrator_service` 可在不启动两个可选服务的情况下部署

### Requirement: 迁移保持网络业务契约
目录迁移 SHALL 保持现有 HTTP/WebSocket 路径、方法、请求字段、响应字段、默认端口、Kafka 消息语义和算子注册协议不变。

#### Scenario: 迁移前后契约对比
- **WHEN** 契约测试比较迁移前基线与迁移后四个服务
- **THEN** 所有对外网络契约保持一致，差异仅限内部路径、导入和构建方式

### Requirement: 测试适应同名 app 包
服务测试 SHALL 在各自服务环境中独立运行；平台契约测试 SHALL 通过根目录唯一项目包、公共契约、子进程、Compose 或 HTTP 边界隔离服务，不得把多个顶级 `app` 解析为同一个模块。

#### Scenario: 分服务测试
- **WHEN** 验证脚本依次运行四个服务的测试
- **THEN** 每组测试使用对应服务目录和环境，并且不受其他服务的 `app` 模块缓存影响

#### Scenario: 跨服务契约验证
- **WHEN** 平台测试验证两个或以上服务的协作
- **THEN** 测试通过公共契约、子进程、Compose 或 HTTP 边界完成验证

### Requirement: 所有路径引用同步迁移
构建、部署、测试、脚本、README、设计文档、运维文档、Harness 和有效 OpenSpec 工件 SHALL 使用新根目录路径。

#### Scenario: 旧路径门禁
- **WHEN** 验证脚本搜索有效源码和交付文件
- **THEN** 不存在仍用于运行、导入或构建的 `algorithm-scheduling-platform/services` 与 `services.<service_name>` 引用

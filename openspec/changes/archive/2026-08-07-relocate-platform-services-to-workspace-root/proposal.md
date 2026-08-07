## 为什么

四个调度平台服务虽然已经具备 FastAPI 目录骨架，但仍嵌套在 `algorithm-scheduling-platform/services` 中，并通过 `services.*` 包前缀、整体 Docker 构建上下文和集中测试相互耦合，不符合后续逐个交付、逐个部署和从服务目录直接运行的目标。当前运行时闭环尚处于早期阶段，现在完成目录和包边界迁移，可以避免 PostgreSQL、Kafka、DAG 和视觉运行时代码继续固化旧路径。

## 变更内容

- **BREAKING** 将 `control_service`、`orchestrator_service`、`vision_orchestrator_service` 和 `online_gateway_service` 从 `algorithm-scheduling-platform/services` 移动到工作区根目录。
- **BREAKING** 移除四个服务对 `services.<service_name>` Python 包前缀和旧兼容入口的依赖，统一从各自项目目录使用 `app.main:app` 启动。
- 让每个服务独立拥有并使用自己的 `app/`、`tests/`、`docker/Dockerfile`、`config.toml`、`requirements.txt` 和 `README.md`，Docker 构建不再复制全部四个服务。
- 保留平台公共契约、公共基础设施、数据库迁移、部署编排和 Harness，并将公共 Python 包作为四个服务的显式构建依赖。
- 更新 Compose、测试、脚本、README、设计文档、Harness 和现有 OpenSpec 变更中的旧目录引用。
- 初始化工作区根 Git 仓库，绑定 `git@github.com:ZhangShen55/algorithm-scheduling.git`，在首次提交前建立大文件、运行产物、缓存和秘密信息排除规则。
- 保持现有 HTTP/WebSocket 路径、方法、字段、端口、算子代码和业务行为不变。

## 能力范围

### 新增能力

- `root-level-platform-services`: 规定四个调度服务位于工作区根目录，并可从各自目录独立安装、测试、运行和构建镜像。
- `workspace-git-baseline`: 规定工作区根 Git 仓库、远端绑定、忽略规则和首次可审查基线的要求。

### 调整能力

无。本次变更不修改对外业务契约；现有规范尚未同步到 `openspec/specs` 主规范目录。

## 影响范围

- 代码：`algorithm-scheduling-platform/services/**`、`algorithm-scheduling-platform/packages/**` 以及四个服务内部导入路径。
- 构建部署：四个 Dockerfile、`docker-compose.platform.yml`、构建上下文、配置挂载和启动命令。
- 测试与治理：平台测试、服务测试、Harness、OpenSpec 变更、README、运维与设计文档。
- 版本控制：工作区根目录新增 Git 元数据和 `.gitignore`，远端为 `git@github.com:ZhangShen55/algorithm-scheduling.git`。
- 不受影响：A 服务请求/查询契约、算子业务接口、算子主动注册协议、Kafka 消息语义、PostgreSQL 逻辑模型和 `/data` 文件布局。

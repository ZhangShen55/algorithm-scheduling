## Context

当前四个服务位于 `algorithm-scheduling-platform/services`，在文件层面已有 `app/`、测试、配置和 Dockerfile，但运行入口和业务模块仍大量使用 `services.<service_name>` 导入。Dockerfile 复制整个 `services` 目录，平台 `pyproject.toml` 也将 `services*`、`packages*` 和 `scripts*` 打包为一个分发物。这意味着服务看起来独立，实际上仍依赖原单体包布局。

工作区根目录已经采用“一个算法算子一个目录”的交付方式。四个调度服务也需要逐个部署，且 `vision_orchestrator_service` 与 `online_gateway_service` 可以按交付需求省略。迁移必须保留 A 面契约、算子调用契约、服务端口和公共平台语义，同时不能依赖符号链接、临时 `PYTHONPATH` 或特定的父目录启动方式。

此外，工作区目前没有 Git 元数据，却包含模型权重、媒体样例、日志、缓存、虚拟环境和生成文件。目录迁移前需要建立可恢复、可审查的版本基线，但不能将大模型、秘密信息和运行产物直接加入仓库。

## Goals / Non-Goals

**Goals:**

- 将四个服务迁移到工作区根目录，并保持四个独立进程和容器边界。
- 每个服务均可从自身目录使用 `app.main:app` 完成安装、导入、测试和启动。
- 让服务内部代码只通过 `app.*` 访问本服务模块，通过已安装的公共分发包访问跨服务契约。
- 让每个 Docker 镜像只包含当前服务、运行依赖和必要公共包。
- 更新所有受影响的构建、部署、测试、文档、Harness 和 OpenSpec 路径。
- 建立安全的根 Git 仓库并绑定用户提供的 GitHub SSH 远端。

**Non-Goals:**

- 不实现 PostgreSQL repository、Kafka runtime、DAG 执行器或视觉运行时闭环。
- 不修改 A 服务、算子 HTTP/WebSocket、Kafka 消息、数据库逻辑模型或结果目录契约。
- 不合并四个服务，也不要求可选服务随核心服务一起部署。
- 不拆分或发布独立的公共 PyPI 包；本阶段只形成工作区内可安装的公共分发包。
- 不自动向远端执行 `git push`；远端推送在迁移验证完成后由用户明确确认。

## Decisions

### 1. 四个服务直接成为工作区根目录项目

目标布局为：

```text
算法功能调度/
├── control_service/
├── orchestrator_service/
├── vision_orchestrator_service/
├── online_gateway_service/
├── algorithm-scheduling-platform/
│   ├── packages/
│   ├── migrations/
│   ├── deploy/
│   ├── harness/
│   └── tests/
├── asr_offline/
├── asr_online/
└── ...
```

`algorithm-scheduling-platform` 暂时保留公共包、数据库迁移、部署编排、跨服务契约测试和 Harness。此次不顺带重命名这些支撑目录，以限制迁移范围。

备选方案是只整理原 `services` 目录，但它仍会让独立服务依赖共同父包，不能解决逐个构建和从自身目录启动的问题。

### 2. 服务使用本地 `app` 包，移除 `services.*` 兼容包

每个服务把现有根级兼容模块中的业务实现归入相应的 `app/api`、`app/application`、`app/domain` 或 `app/infrastructure`。`app` 内部使用包相对导入，使服务既能从自身目录按顶级 `app` 运行，也能在工作区契约测试中按 `control_service.app` 等唯一项目名称导入；唯一部署启动入口为：

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port PORT --workers 1
```

不保留 `services.<service_name>` 导入兼容层，因为它会继续要求旧父目录存在。根目录项目包只用于源码定位和测试隔离，不新增第二个部署入口。服务移动属于内部构建契约变更，外部网络契约保持不变。

### 3. 公共代码作为显式安装依赖

`algorithm-scheduling-platform/pyproject.toml` 调整为只发布真正的公共 Python 包，不再包含 `services*`。本地开发通过正常的 editable install 安装公共分发包；Docker 构建从工作区上下文复制公共包源并执行 `pip install`，不使用 `PYTHONPATH`。

每个服务的 Dockerfile 只复制：

- 当前服务的 `requirements.txt` 和 `app/`；
- 当前服务运行需要的配置或静态资源；
- `algorithm-scheduling-platform` 中的可安装公共包。

它不得复制其他三个服务。这样既保留共享契约，又使镜像内容与部署边界一致。

备选方案是把公共代码复制四份，但会导致状态码、响应模型、repository 和指标实现产生漂移，因此不采用。

### 4. 单服务测试与跨服务测试分层执行

四个项目部署时都包含名为 `app` 的顶级包。服务自身测试从该服务目录独立执行；平台契约测试可以通过根目录唯一项目包导入服务入口，运行时集成仍优先通过子进程、Compose 或 HTTP 边界验证，不把多个服务源码合并成一个部署包。

迁移验收至少包括：

- 四个服务分别 `compileall` 和导入 `app.main:app`；
- 四个服务各自测试通过；
- 平台非集成测试和契约测试通过；
- Compose 配置可解析且构建路径存在；
- 每个服务可单独构建镜像并启动健康检查；
- 搜索不到有效代码、构建和运维命令中的旧 `algorithm-scheduling-platform/services` 或 `services.<service>` 引用。

### 5. 先建立可审查 Git 基线，再执行目录迁移

实施阶段先审计文件大小和秘密信息，创建根 `.gitignore`，初始化 Git，并将 `origin` 设置为 `git@github.com:ZhangShen55/algorithm-scheduling.git`。首次基线只跟踪源代码、安全默认配置、迁移、测试、文档、OpenSpec 和 Harness；模型、媒体、日志、虚拟环境、缓存和运行结果不得进入索引。

在移动前形成一个可恢复基线提交，然后使用 Git 感知的移动完成目录迁移，并形成单独迁移提交。提交前必须检查暂存文件和大文件清单。远端推送不属于自动实施步骤。

### 6. 迁移不改变服务和算子职责

`control_service` 继续负责任务事实、事务内 Outbox 写入、实例注册与容量租约；`orchestrator_service` 继续负责 Outbox 发布、Kafka、DAG 和通用节点执行。视觉与在线服务仍为可选部署。算子的现有业务接口和 `operator_registry_client` 注册协议不因目录移动而改变。

## Risks / Trade-offs

- [风险] 四个 `app` 包在工作区根测试进程中发生模块缓存冲突 → 服务测试在各自目录单独运行，跨服务验证改为子进程或网络边界。
- [风险] 公共包仍位于 `algorithm-scheduling-platform`，独立服务并非完全零依赖 → 将其定义为显式可安装依赖并锁定边界；是否拆成独立仓库留待后续交付演进。
- [风险] 旧路径散布在约 70 个文件中，漏改会导致 Docker 或运维命令失败 → 使用全工作区 `rg` 门禁并执行 Compose、导入、测试和镜像构建验证。
- [风险] 首次 Git 提交误收模型、媒体或秘密 → 先建立 `.gitignore`，再按文件大小、扩展名和敏感名称审计索引，禁止直接 `git add .` 后无检查提交。
- [风险] 当前无 Git 历史，迁移失败难以恢复 → 在移动前创建安全基线提交，迁移使用 Git 感知移动，不删除用户文件。
- [权衡] 此次保留 `algorithm-scheduling-platform` 支撑目录，不能一次消除全部“杂乱感” → 优先解决可部署服务边界；支撑目录重命名或再分层作为后续独立变更。

## Migration Plan

1. 审计工作区大文件、秘密、缓存、运行产物和现有忽略文件。
2. 创建根 `.gitignore`，初始化 Git，配置 `origin`，生成并检查移动前安全基线。
3. 将四个服务移动到工作区根目录，不触碰算法算子目录。
4. 把服务业务模块归入 `app` 分层并转换为 `app.*` 导入，删除旧 `services.*` 兼容入口。
5. 将平台公共项目收敛为显式可安装依赖，并更新四个服务依赖与 Docker 构建。
6. 更新 Compose、脚本、测试、README、运维文档、设计文档、Harness 和 OpenSpec 路径。
7. 分别验证四个服务，再验证平台契约、Compose 和镜像构建。
8. 检查 Git 暂存内容、变更统计和大文件，形成迁移提交；得到用户确认后再推送远端。

回滚时回到移动前基线提交即可恢复原目录。运行时契约未变化，因此不涉及数据库、Kafka 或线上数据回滚。

## Open Questions

无阻塞问题。`algorithm-scheduling-platform` 支撑目录是否后续改名，可在本次迁移完成后单独讨论。

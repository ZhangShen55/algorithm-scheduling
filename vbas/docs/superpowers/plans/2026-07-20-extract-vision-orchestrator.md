# vision_orchestrator 独立仓库实施计划

> **执行要求：** 按步骤完成独立仓库抽取、重命名、验证和推送。

**目标：** 将最新 ai_quality 通用能力抽取为独立的 `jy-vision-orchestrator-server` 仓库，并将 Python 包重命名为 `vision_orchestrator`。

**架构：** 新仓库只保留 Kafka消费、视频准备、TIAS注册调度、结果聚合、快照和数据库能力。TIAS推理只通过 HTTP接口访问，不保留对 `tias.services` 的源码导入。部署配置使用示例文件，真实环境密码不进入 Git。

**技术栈：** Python 3.11、FastAPI、Kafka、Redis、MySQL、OpenCV、Docker、Cython。

---

### 任务一：创建独立仓库工作目录

- [ ] 克隆空仓库 `git@github.com:ZhangShen55/jy-vision-orchestrator-server.git`。
- [ ] 创建 `main` 分支。
- [ ] 从 `dev_6.0_ai_quality_910b_npu` 提取最新 `ai_quality/` 和 Cython构建脚本。
- [ ] 删除真实 `config.toml`，只保留 `config.toml.example`。

### 任务二：完成服务重命名和依赖解耦

- [ ] 将包目录改为 `vision_orchestrator/`。
- [ ] 更新 Python导入、Docker构建路径、启动命令和部署文档。
- [ ] 配置优先读取 `[Vision_Orchestrator]`，并兼容旧 `[AI_Quality]`。
- [ ] 新增 `VISION_ORCHESTRATOR_WORKER_ID`，并兼容旧环境变量。
- [ ] 删除本地推理适配器及 `tias.services` 源码依赖，只保留远程 TIAS调用。
- [ ] 保留 Kafka消费组和 HTTP接口路径，避免外部协议无关变更。

### 任务三：补齐独立交付文件

- [ ] 创建仓库根目录 README、`.gitignore` 和 `.dockerignore`。
- [ ] 调整 Dockerfile，使构建上下文为新仓库根目录。
- [ ] 调整 Compose中的镜像、容器、配置挂载和启动命令。
- [ ] 迁移 ai_quality单元测试并更新导入路径。

### 任务四：验证并推送

- [ ] 搜索并确认不存在 `from tias` 或 `import tias`。
- [ ] 验证配置加载、新旧配置兼容和 Docker上下文路径。
- [ ] 运行全部单元测试。
- [ ] 检查 Git差异中不存在真实密码、密钥和部署配置。
- [ ] 使用中文规范提交信息创建初始提交。
- [ ] 推送 `main` 到新仓库。

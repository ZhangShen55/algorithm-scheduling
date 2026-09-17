## ADDED Requirements

### Requirement: 升级必须从可追溯的隔离基线开始
实施 SHALL 把 `FaceRec.git` 的 `v1.3` 分支克隆到工作区同级临时目录 `facerec_v1.3/`，记录远端 URL、分支和精确提交，并在该目录完成改造与首次验证。临时 `.git` MUST NOT 成为父仓库 gitlink 或最终 `facerec/` 内容。

#### Scenario: v1.3 分支与预期提交一致
- **WHEN** 远端 `v1.3` 仍解析为提案检查的 `980e81b7a0bf9bffc07cd5c338a3f91eef1a019d`
- **THEN** 实施记录该 SHA 并以其作为上游算法基线

#### Scenario: v1.3 分支已移动
- **WHEN** 实施时远端 `v1.3` 不再解析为预期 SHA
- **THEN** 实施不得静默继续，必须记录新旧提交差异并先更新 change 范围

### Requirement: 模型资产必须完整且不得进入 Git
实施 SHALL 从 `/Volumes/Data55/算子功能部署/facerec/ai_models` 向临时项目注入完整模型，并逐文件验证规定相对路径、非零大小和与来源一致。模型二进制、外部可信 manifest、部署 tar 和人脸原图 MUST NOT 被父仓库跟踪。

#### Scenario: 完整模型注入
- **WHEN** 外置目录包含 ArcFace、Dlib 68 点和 `buffalo_l` 五个 ONNX 文件
- **THEN** 临时项目获得七个字节一致的模型二进制，模型加载测试可执行，父仓库状态不列出这些二进制

#### Scenario: 模型来源不完整
- **WHEN** 任一必需文件缺失、为空或复制后不一致
- **THEN** 验证立即失败，不进入源码替换阶段

### Requirement: 替换前必须完成分层验证
临时项目 SHALL 依次通过 compileall、`from app.main import app`、依赖检查、单元测试、契约测试、启动、health/readiness、MongoDB 集成、真实图片推理、严格 GPU 负向测试、`192.168.29.11` GPU 容器真实推理、平台租约调用及路由方法对比。每项证据 MUST 记录命令、环境、结论和必要的脱敏摘要。

#### Scenario: 所有门禁通过
- **WHEN** 每个必需检查都有当前基线上的成功证据且无未解释失败
- **THEN** 临时项目被标记为可替换候选，并进入最终目录迁移

#### Scenario: 任一门禁失败
- **WHEN** 任一测试、启动、readiness、真实推理、GPU 或契约检查失败
- **THEN** 当前 `facerec/` 保持不变，任务记录失败原因并继续在隔离目录修复

### Requirement: 最终迁移必须使用明确允许清单
隔离候选全部通过后，实施 SHALL 移除旧 `facerec/` 受管内容，剥离 `facerec_v1.3/` 的嵌套 `.git`，并把该目录改名为唯一正式 `facerec/`。迁移 SHALL 只纳入经验证的源码、测试、根配置示例、requirements、Docker、脚本和文档，MUST 排除模型二进制、日志、媒体、cache、编译产物、部署 tar 和本地凭据，并 SHALL 保留 `app.main:app` 入口。

#### Scenario: 检查迁移结果
- **WHEN** 候选内容进入最终 `facerec/`
- **THEN** 父仓库只存在一个名为 `facerec` 的正式项目并把源码视为普通受管文件，不存在 `facerec_v1.3`、嵌套仓库或 gitlink，忽略资产未进入 diff

#### Scenario: 发现最终目录存在新的用户修改
- **WHEN** 替换前 `facerec/` 出现不属于本 change 的未提交修改
- **THEN** 自动替换停止，实施者先区分并保留用户修改，不得清理或覆盖

### Requirement: 最终路径必须重新验证
临时目录的成功证据不得替代最终 `facerec/` 路径验证。迁移后 SHALL 在 `facerec/` 中重新执行规定 Conda 环境的 compileall、导入、测试、uvicorn、health/readiness、真实识别、路由兼容、平台注册和日志检查。

#### Scenario: 临时目录通过但最终路径失败
- **WHEN** 候选在 `facerec_v1.3/` 通过而在 `facerec/` 因路径、配置或打包差异失败
- **THEN** 替换不得标记完成，实施回到迁移步骤修复并重跑全部受影响门禁

### Requirement: 失败必须可精确回退且不影响其他项目
实施 SHALL 在替换前记录父仓库基线和 `facerec/` 精确差异。最终验证失败时 SHALL 只回退本 change 对 FaceRec 的修改，保留失败证据，并 MUST NOT reset、clean、删除或改写 ASR Online、Text Analysis、其他算子、平台服务或 OpenSpec change 的用户修改。

#### Scenario: 最终替换验证失败
- **WHEN** 最终 `facerec/` 未通过必需门禁
- **THEN** 当前可运行 FaceRec 内容可从记录基线恢复，其他工作树修改与未跟踪文件保持原样

### Requirement: 成功后必须清理临时升级资产
最终 `facerec/` 通过全部门禁后，实施 SHALL 确认临时 `facerec_v1.3/` 已因正式改名而不存在，父仓库只保留最终项目和本 change 工件。清理 MUST NOT 删除外置模型来源。

#### Scenario: 升级完成清理
- **WHEN** 最终路径验证和替换证据全部完成
- **THEN** 临时克隆不再污染工作区，`/Volumes/Data55/算子功能部署/facerec/ai_models` 保持不变且可用于后续部署

### Requirement: 远端构建必须保留 BuildKit cache
`192.168.29.11` 上的候选和最终 FaceRec 构建 SHALL 复用并保留既有 Docker BuildKit cache，MUST NOT 使用 `--no-cache`，MUST NOT 执行 `docker builder prune`、`docker buildx prune` 或 `docker system prune`。Harness SHALL 记录构建前后 cache 摘要、构建命令、镜像完整 ID 和 revision。

#### Scenario: 使用缓存完成最终构建
- **WHEN** 在指定服务器构建最终 `algorithm-facerec` 镜像
- **THEN** 构建命令未禁用 cache，已有 layer 可复用，构建后 cache 仍存在且其他镜像、容器和卷未因构建被清理

#### Scenario: 磁盘空间不足
- **WHEN** 构建前磁盘门禁不足以安全完成构建
- **THEN** 实施停止并报告阻断，不以 prune 或删除 BuildKit cache 绕过门禁

### Requirement: 最终运行资产必须统一使用 FaceRec 正式名称
最终 Git 目录 MUST 为 `facerec/`，镜像 repository MUST 为 `algorithm-facerec`，Compose service 和实例 MUST 保持 `facerec-gpu0`、`facerec-gpu1`、`facerec-gpu2`。`v1.3` 只允许出现在版本、tag、revision 和历史证据中，不得残留在最终项目、service、container 或 repository 基名中。

#### Scenario: 最终命名盘点
- **WHEN** 最终三实例部署并注册完成
- **THEN** Git、Compose、Docker 和 Control Service 中的活动 FaceRec 资产均使用正式名称，且不存在活动 `facerec_v1.3` 资产

### Requirement: 旧 FaceRec 容器和镜像必须在验收后精确删除
新三实例通过 GPU、health/readiness、注册、真实识别、Online Gateway 租约和清理前 Smoke 后，实施 SHALL 使用预先记录并审核的完整容器/镜像 ID 精确删除被替换的旧 FaceRec 容器和镜像。清理 MUST 保留 BuildKit cache、模型、卷、MongoDB、平台中间件、其他算子和无关镜像，并 SHALL 在清理后重跑 Smoke。

#### Scenario: 新版本尚未全部通过
- **WHEN** 任一新 FaceRec 实例或平台调用门禁未通过
- **THEN** 旧容器和镜像保持可回滚，不执行删除

#### Scenario: 精确清理完成
- **WHEN** 新版本全部通过且 dry-run 清单中的旧资产身份仍与现场一致
- **THEN** 只删除清单中的旧 FaceRec 容器和镜像，清理后 Smoke 通过且 BuildKit cache、卷、模型和无关资产不变

### Requirement: 交付必须使用中文规范提交并补充 Harness
实施 SHALL 使用中文 Conventional Commit 分阶段提交本 change 拥有的文件并推送到选定集成分支。Harness MUST 新增 `harness/scenarios/facerec-v1-3-upgrade-20260917.md`，同步 `harness/verification.md` 和 `harness/change-ledger.md`，并记录绑定实际测试 Git SHA 的远端平台、构建缓存、镜像/容器身份、三实例 GPU/注册、真实租约、清理前后 Smoke 和旧资产精确清理证据。

#### Scenario: 提交与推送
- **WHEN** 一个实现或验证阶段达到独立可验证状态
- **THEN** 只提交该阶段拥有的文件，提交标题使用中文 Conventional Commit，并在推送后记录远端分支和提交 SHA

#### Scenario: Harness 证据收口
- **WHEN** `192.168.29.11` 的最终部署和清理后 Smoke 完成
- **THEN** 场景、验证命令、变更账本和远端脱敏证据一致，Harness consistency、OpenSpec strict 和 `git diff --check` 全部通过

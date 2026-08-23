## ADDED Requirements

### Requirement: 镜像清理前必须建立完整库存和保护集
发布清理系统 SHALL 在任何删除前快照全部容器、完整镜像 ID、RepoTag、RepoDigest、Compose project/service、运行状态和镜像 revision。保护集 MUST 包含全部运行容器引用镜像、当前发布的 11 个目标镜像、当前回滚基线、必需基础镜像和显式允许列表。

#### Scenario: 容器引用镜像进入保护集
- **WHEN** 库存显示某一运行或明确保留容器引用某完整镜像 ID
- **THEN** 该镜像 ID SHALL 进入保护集且不得出现在删除候选集

#### Scenario: 库存不完整时失败关闭
- **WHEN** 任一容器或镜像无法 inspect、镜像 ID 不完整或 Compose 身份无法解析
- **THEN** 清理系统 SHALL 停止计划生成并不执行删除

### Requirement: 删除候选必须是保护集的可审计差集
发布清理系统 SHALL 只把不在保护集、无保留容器引用、且可证明属于旧版或悬空构建的完整镜像 ID 放入候选集。每个候选项 MUST 带删除原因、关联标签/digest、归属 release 和预估回收空间。

#### Scenario: 悬空镜像按 ID 成为候选
- **WHEN** `<none>:<none>` 镜像没有容器引用、不在基础镜像允许列表且不属于当前 release
- **THEN** 它可以按完整镜像 ID 进入候选集，但不得仅依靠 `<none>:<none>` 文本做删除目标

#### Scenario: 候选与保护集交集非空
- **WHEN** 清理计划校验发现任一候选镜像 ID 同时存在于保护集
- **THEN** 整个清理计划 MUST 失败关闭，不能只跳过该单项后继续删除

### Requirement: 镜像清理必须先 dry-run 并防止状态漂移
发布清理系统 SHALL 先原子发布 dry-run 计划，然后重新获取容器和镜像状态。只有保护集、候选集、引用关系和操作授权都没有漂移时，系统才可以逐个完整 ID 删除。

#### Scenario: dry-run 后新容器引用候选镜像
- **WHEN** dry-run 发布后发现新容器开始引用候选镜像
- **THEN** 执行阶段 SHALL 因状态漂移中止全部删除，要求重新生成计划

#### Scenario: dry-run 审核通过
- **WHEN** 操作者确认同一 release/Git SHA 的 dry-run 计划且二次 inspect 无漂移
- **THEN** 清理系统 SHALL 只执行计划中列出的完整容器/镜像 ID

### Requirement: 构建前和新版验收后必须分两阶段清理
发布清理系统 SHALL 支持构建前空间回收和新版验收后旧版退役两个独立阶段。构建前 MUST 保留当前可运行基线；验收后只有新版 11 镜像、21 算子实例、四平台服务与必需 Campaign 阶段符合时，才能退役上一版。

#### Scenario: 构建前保留当前基线
- **WHEN** 新版镜像尚未全部构建与 Smoke 通过
- **THEN** 构建前清理 SHALL 不删除当前回滚基线使用的容器和镜像

#### Scenario: 新版未符合时禁止退役旧版
- **WHEN** 新版任一必需预检、Smoke、Campaign 阶段或报告聚合失败
- **THEN** 验收后清理 MUST 不删除上一版容器和镜像

#### Scenario: 新版符合后精确退役
- **WHEN** 新版全部必需门禁符合且生成待退役容器的不可变 inspect/digest/release 证据
- **THEN** 清理系统 SHALL 先按完整容器 ID 删除已停止旧容器，再重算候选集并按完整镜像 ID 删除旧镜像

### Requirement: 精确清理不得伤害数据、模型和历史证据
发布清理系统 MUST 禁止 `docker system prune -a`、`docker compose down -v`、未解析环境变量、glob 或宽泛名称匹配作为删除目标。它 MUST 保留 PostgreSQL、Kafka、Redis、MongoDB 数据卷、模型资产、`/data/result`、Git 源码、原始 release 报告和审计证据。

#### Scenario: 清理计划包含数据卷或持久目录
- **WHEN** 清理计划出现 Docker volume、`/data/result`、模型目录、Git 目录或 release 证据路径
- **THEN** 计划校验 MUST 失败关闭并不允许执行任何删除

#### Scenario: 禁止宽泛 prune
- **WHEN** 操作请求尝试通过 prune 或宽泛名称/标签匹配执行发布清理
- **THEN** 清理入口 SHALL 拒绝该请求并要求提供经 dry-run 审核的完整 ID 计划

### Requirement: 清理结果必须可追溯且不能伪造成功
发布清理系统 SHALL 为每个删除目标记录完整 ID、删除前快照摘要、执行时间、成功/失败、错误原因和删除后二次验证，并记录 `docker system df` 前后差异。任一目标删除失败 MUST 保留原始状态并使清理阶段不符合。

#### Scenario: 部分删除失败
- **WHEN** 候选集中任一完整镜像 ID 因新引用或 Docker 错误无法删除
- **THEN** 清理结果 SHALL 记录已成功和未删除集合，把阶段标记为不符合，且不得通过修改 dry-run 文件掩盖失败

#### Scenario: 清理后新版仍就绪
- **WHEN** 验收后清理计划全部执行完成
- **THEN** 系统 SHALL 重新验证基础设施、四平台服务、21 个算子实例、注册/租约、GPU 归属和 7/7 Smoke，只有全部就绪才能发布清理完成结论

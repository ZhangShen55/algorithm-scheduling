## ADDED Requirements

### Requirement: 目标机发布必须升级受影响镜像和容器
实现完成并通过本地门禁后，部署流程 SHALL 在 `192.168.29.11` 为实际发生代码变化的 Control Service 和运维控制台构建带 Git revision 的新镜像，并 SHALL 使用新镜像替换对应容器。部署不得无理由重建或重启 online-gateway-service、orchestrator-service、vision-orchestrator-service、七类算子、GPU exporter 或基础设施。

#### Scenario: 发布前后版本可核对
- **WHEN** 新版 Control Service 和运维控制台完成替换
- **THEN** 发布记录包含 Git 完整 SHA、新旧镜像完整 ID/digest、新旧容器完整 ID、Compose 身份、健康状态和重启次数

### Requirement: 旧容器和旧镜像只能在新版验收后精确删除
部署流程 MUST 在替换前记录旧资产并建立保护集。只有新版 revision、健康、readiness、A 服务兼容、运维接口 Smoke 和前端真实数据验收全部通过后，才 SHALL 按完整 ID删除本次被替代且不再被任何容器引用的旧容器和旧镜像。流程 MUST 禁止宽泛 prune、模糊名称匹配和强制删除。

#### Scenario: 新版全部门禁通过
- **WHEN** 新版所有必需验收通过且 dry-run 确认旧资产不属于当前发布或其他容器引用
- **THEN** 部署按完整容器 ID和镜像 ID删除被替代资产，随后复核当前容器健康并记录清理清单和释放空间

#### Scenario: 新版门禁失败
- **WHEN** 构建、revision、健康、readiness、兼容或业务 Smoke 任一步失败
- **THEN** 部署停止旧资产清理、保存失败证据并使用仍保留的旧镜像完整 ID回滚

#### Scenario: 保护其他运行资产
- **WHEN** 目标机同时存在基础设施、算子、GPU exporter、基础镜像、BuildKit 缓存、volume、模型或数据目录
- **THEN** 清理流程不得删除或修改这些保护资产，也不得执行 `docker system prune`、`docker image prune -a` 或 `docker builder prune`

### Requirement: 发布必须形成 Harness、中文提交和远端分支证据
部署完成后，项目 SHALL 在 `algorithm-scheduling-platform/harness/scenarios/` 记录可复现命令、测试结果、新旧版本身份、远端响应摘要、清理 dry-run/执行结果、健康复核、回滚边界和未覆盖项。提交 SHALL 精确包含本变更文件而保留其他工作区改动，并 SHALL 使用中文 Conventional Commit 提交信息推送当前 `codex/` 分支。

#### Scenario: 发布与 Git 闭环
- **WHEN** 远端升级、验收和旧资产精确清理完成
- **THEN** Harness 包含完整证据，OpenSpec 严格校验和 `git diff --check` 通过，远端分支指向记录的中文规范提交 SHA

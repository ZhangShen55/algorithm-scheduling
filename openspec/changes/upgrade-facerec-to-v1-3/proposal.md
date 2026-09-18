## 为什么

当前工作区中的 `facerec` 仍以 Dlib 单人脸检测为主，缺少 `git@github.com:ZhangShen55/FaceRec.git` 的 `v1.3` 分支已经具备的 InsightFace `buffalo_l` 检测和多人脸识别能力。需要在不破坏既有平台集成、依赖边界和 HTTP 契约的前提下，以隔离目录完成升级、真实推理验证和受控替换。

## 变更内容

- 从 `FaceRec.git` 的 `v1.3` 分支建立临时验证项目 `facerec_v1.3/`，固定并记录基线提交，不直接覆盖当前 `facerec/`。
- 将上游源码整理为工作区规定的真实 `app` 包和 `app.main:app` 入口，禁止依赖目录名、临时符号链接或临时 `PYTHONPATH`。
- 引入 InsightFace `buffalo_l` 检测、多人脸识别及 v1.3 的匹配行为，并保留 Dlib 兼容路径；上游新增的 `points`/ROI 请求能力不进入本次兼容升级。
- 不引入上游 FaceRec 直连 Redis 的 embedding cache；MongoDB 继续作为人员事实和识别候选来源，平台 Redis 仍只由 `control_service` 访问。
- 使用 `/Volumes/Data55/算子功能部署/facerec/ai_models` 中的完整模型资产进行验证；模型二进制继续由 Git 忽略，不进入仓库。
- 移植并保留当前 `facerec` 的平台注册与容量、统一 JSON Lines 日志、运行目录、MongoDB 配置覆盖、就绪检查、Dlib 进程隔离、严格 GPU 校验和人脸原图默认不持久化等平台能力。
- 保持既有 HTTP 路径、方法、请求字段、响应字段、业务状态码和默认端口；不把上游 `statusCode`、`hasFace` 等驼峰字段作为替代合同。
- 补齐 InsightFace 运行依赖、根配置、Docker 构建和部署说明，并保证严格 GPU 部署不会静默回退 CPU。
- 统一 FaceRec 主进程和 InsightFace 检测 worker 的 GPU 进程名为 `facerec`，不得在 `nvidia-smi` 中暴露内部 Python 解释器绝对路径。
- 在 `facerec_v1.3/` 中完成静态检查、单元/契约测试、启动与健康检查、模型就绪检查、学生与教师场景无关的 FaceRec 真实图片推理及路由兼容性验证。
- 平台适配、三实例 GPU 部署、真实租约调用和最终验收 MUST 在 `192.168.29.11` 完成，使用工作区已批准的固定登录合同，登录凭据不得进入 Harness 普通证据。
- 远端 Docker build MUST 复用并保留既有 BuildKit cache，不使用 `--no-cache`，不执行 builder、buildx 或 system prune。
- 隔离验证全部通过后，移除旧 `facerec/` 内容并将去除嵌套 Git 元数据的 `facerec_v1.3/` 改名为唯一正式目录 `facerec/`；最终目录、镜像 repository、Compose service 和容器不得保留 `facerec_v1.3` 名称。
- 新 `facerec` 在平台通过最终验收后，按完整容器/镜像 ID 精确删除被替换的旧 FaceRec 容器和镜像，保留模型、卷、BuildKit cache、平台中间件和无关业务资产。
- 实施使用中文 Conventional Commit 提交并推送，补充绑定实际测试 Git SHA 的 Harness 场景、验证命令、变更账本和远端脱敏证据。

## 能力

### 新增能力

- `facerec-v1-3-inference`：规定 InsightFace `buffalo_l`、多人脸、ArcFace embedding、Dlib 兼容路径和识别结果兼容行为，并明确排除会扩展旧请求模型的 `points`/ROI。
- `facerec-v1-3-platform-integration`：规定 v1.3 实现必须满足本工作区的包结构、配置、日志、注册、容量、就绪、GPU、存储和依赖边界。
- `facerec-v1-3-safe-replacement`：规定隔离克隆、模型注入、验证证据、替换门禁、清理和回滚行为。

### 修改能力

本次不修改主规范库中的既有能力要求；与 FaceRec 相关的平台合同作为新 v1.3 能力的兼容门禁进行重申和验证。

## 影响

- 主要影响 `facerec_v1.3/` 临时验证目录、最终 `facerec/` 项目、FaceRec Docker/Compose 配置、项目测试和相关 README。
- 新增 `insightface`、`onnxruntime-gpu` 等推理依赖；MongoDB 仍是人员事实与 embedding 的唯一持久化来源，FaceRec 不直连平台 Redis。
- 模型目录由约 344 MiB 扩展到约 677 MiB，新增 `ai_models/models/buffalo_l/` 下五个 ONNX 文件；这些文件不得提交 Git 或写入普通日志。
- `online_gateway_service`、Control Service 的 FaceRec 注册合同、现有调用方以及 `/recognize`、`/recognize/batch`、`/persons`、`/ops/*` 等路径不得因升级发生不兼容变化。
- 影响 `algorithm-scheduling-platform` 的 FaceRec 构建/部署验证和 Harness 文档；最终远端证据必须来自 `192.168.29.11` 并绑定实际部署镜像 revision。
- 当前工作区中与 ASR Online 等其他项目有关的未提交修改不属于本 change，实施和替换过程不得改写或清理这些文件。

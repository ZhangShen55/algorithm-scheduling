## 背景

当前 `facerec/` 位于 `algorithm-scheduling` 单仓内，已经完成真实 `app` 包、平台注册与容量、统一日志、运行目录、就绪检查、Dlib 子进程隔离、严格 GPU 配置和 `save_person_photo=false` 等平台化改造，但检测路径仍以 Dlib 单人脸为主。独立仓库 `git@github.com:ZhangShen55/FaceRec.git` 的 `v1.3` 分支在提案建立时解析为 `980e81b7a0bf9bffc07cd5c338a3f91eef1a019d`，包含 InsightFace `buffalo_l`、多人脸、ROI 和 Redis cache，但其源码布局、响应字段、硬编码路径、GPU 回退、FaceRec 直连 Redis 和日志实现不符合当前工作区合同。

完整模型位于 `/Volumes/Data55/算子功能部署/facerec/ai_models`，共包含 ArcFace、Dlib 68 点及 `buffalo_l` 五个 ONNX 文件。当前 `facerec/ai_models` 只有 ArcFace 和 Dlib 文件，二者与外置副本校验一致。模型二进制受 Git 忽略，不能依赖上游仓库下载。

本 change 涉及独立上游、临时验证项目、算法推理、平台运行时、远端部署和最终目录替换，必须先在 `facerec_v1.3/` 隔离验证。唯一正式平台适配与验收主机是 `192.168.29.11`，使用工作区已批准的固定 `root` 登录合同；密码不重复写入 OpenSpec、Harness 或命令证据。当前工作树另有 ASR Online 等无关未提交修改，实施不得清理、覆盖或混入这些修改。

## 目标与非目标

**目标：**

- 以可追溯的 v1.3 提交为源代码基线，获得 InsightFace `buffalo_l` 和多人脸能力。
- 保留当前 FaceRec 的 HTTP/WebSocket 边界、平台集成、配置方式、安全日志、GPU 严格性和存储语义。
- 让临时项目和最终项目都满足 `python -m uvicorn app.main:app` 的标准启动形态。
- 使用完整模型和真实图片完成本地 CPU 推理，并为 GPU 容器执行严格设备与真实推理门禁。
- 仅在隔离项目通过全部门禁后替换 `facerec/`，且替换失败可以精确回退。
- 在 `192.168.29.11` 的既有算子平台上完成三实例平台适配、真实租约调用、最终统一命名、旧资产精确清理和 Harness 取证。

**非目标：**

- 不修改 `online_gateway_service` 的 FaceRec 北向接口、路由策略或容量租约协议。
- 不新增 RTSP、视频拉流、抽帧或课程级编排能力。
- 不迁移、删除或批量重算现有 MongoDB 人员记录和 embedding。
- 不把模型、部署 tar、人脸原图、凭据或完整识别文本写入 Git 和日志。
- 不重构其他算子，不处理当前工作树中与本 change 无关的改动。
- 不让 FaceRec 直接连接平台 Redis，不执行 `--no-cache`、Docker prune、卷删除或无关容器清理。

## 技术决策

### 1. 使用隔离克隆和固定提交，而不是原地升级

在工作区根目录创建 `facerec_v1.3/`，从 `v1.3` 分支克隆并记录实际提交；本 change 以已检查的 `980e81b...` 为预期基线。若实施时远端分支已移动，先记录新旧差异并更新 change，不静默引入未知代码。临时目录保留独立 `.git` 仅用于比较上游，父仓库不得把它登记为 gitlink。

备选方案是直接覆盖 `facerec/` 后修复，但会在改造和验证期间失去当前可运行基线，也难以区分上游缺陷与平台移植回归，因此不采用。

### 2. 以上游 v1.3 为算法基线，定向移植当前平台能力

`facerec_v1.3/` 作为项目根；把运行源码整理到真实 `app/` 包，把 `config.toml`、`requirements.txt`、`docker/`、`tests/`、`docs/`、`media/`、`logs/` 和 `ai_models/` 保持在项目根约定位置。上游算法行为从 v1.3 引入，当前 `facerec` 的平台注册、配置加载、日志、就绪、设备检查、Dlib worker 隔离、运行路径和照片持久化控制按模块定向移植，不做整目录双向覆盖。

备选方案是以当前版本为基线摘取 v1.3 的个别函数，但 v1.3 的检测、多人脸、路由和 cache 改动跨越多个模块，容易遗漏隐式依赖，无法证明实现等同于目标版本。

### 3. 保持现有 API 合同，内部适配 v1.3 结果

所有现有路径和方法保持不变。基线 OpenAPI 证明当前 `/recognize` 请求模型只有 `photo`、`targets` 和 `threshold`，没有上游 v1.3 的可选 `points`；因此本次明确不移植 ROI 请求能力，避免借升级扩展公共合同。响应继续使用当前 `status_code`、`has_face`、`bbox`、`match` 等字段和既有业务状态码。多人脸识别在内部处理全部有效人脸，将人员匹配按现有规则去重、排序并写入既有 `match` 列表；`bbox` 保持代表主脸的兼容语义，不新增或替换字段。`/recognize/batch` 保持单人多帧聚合语义，不与单图多人脸混淆。

备选方案是直接采用上游 `statusCode`、`hasFace`、`bboxs`，但这会改变已有调用方合同，违反工作区兼容边界。

### 4. 统一设备配置并禁止严格 GPU 部署回退

继续以 `[gpu].device = "cpu" | "cuda:N"` 作为 ArcFace 和 InsightFace 的统一设备来源；`[runtime].require_gpu=true` 时，两类模型都必须建立 CUDA provider/runtime，任何 CPU provider 回退都使启动或 readiness 失败。显式 CPU 配置用于 macOS `facerecapi` 环境的本地验证。Dlib 兼容检测器仍可选，但不得绕过严格 GPU 部署对 ArcFace 的要求。

上游分别配置 `gpu_id`、InsightFace device 并在失败时自动回退 CPU，容易出现配置漂移和“看似健康但未用 GPU”的实例，因此不保留。

### 5. 不移植上游 Redis cache，MongoDB 保持人员事实来源

上游 v1.3 的 Redis embedding cache 不进入平台版。FaceRec 从 MongoDB 读取人员事实和 embedding，写操作只提交 MongoDB；平台 Redis 继续只由 `control_service` 管理注册、生命周期和租约。若需要性能优化，只能先取得不改变跨实例一致性的设计与证据，再以独立 change 引入。

直接移植 Redis cache 虽能减少 MongoDB 读取，但会违反当前平台“只有 `control_service` 直连 Redis”的服务边界，并给三个 FaceRec 实例带来 cache 一致性风险，因此本次明确排除。

### 6. 模型通过外置资产注入并逐文件验证

把 `/Volumes/Data55/算子功能部署/facerec/ai_models` 复制到临时项目根 `ai_models/`，核对七个必需二进制的相对路径、非零大小及与来源逐文件一致。Git 只保留模型说明和忽略规则，不提交二进制或外部可信 manifest。Docker 构建必须按既有部署合同决定模型是否进入镜像，并通过构建输入门禁阻止临时仓库、部署 tar、日志和媒体进入上下文。

### 7. 采用分层门禁后替换

门禁依次为：源码与依赖静态门禁、单元/契约测试、无外部服务的负向测试、MongoDB 集成测试、本地 CPU 启动和真实推理、`192.168.29.11` GPU 容器启动和真实推理、OpenAPI 路径/方法/字段对比、平台注册、租约调用与日志检查。任一层失败都停止替换。

最终不是把嵌套仓库整体移动为 `facerec/`，而是将已验证项目内容纳入父仓库受管路径，排除 `.git`、模型二进制、日志、媒体、缓存和部署 tar。替换后在真实 `facerec/` 路径完整重跑门禁，避免目录假设被临时路径掩盖。

### 8. 平台适配和最终验收只在 `192.168.29.11` 完成

本地验证只证明源码、CPU 推理和合同。平台 Compose、三个 `facerec-gpu{0,1,2}` 实例、Control Service 注册、Online Gateway 租约路由、GPU provider、共享 MongoDB 和清理后 Smoke 必须在 `192.168.29.11` 的现有平台上验证。远端执行前先只读记录平台、容器、镜像、GPU、端口和 BuildKit cache 基线；只操作精确识别的 FaceRec 资产。

备选方案是在本地 Compose 或其他 GPU 主机完成验收，但无法证明与现有平台拓扑、三卡实例和租约路由兼容，因此不能作为最终通过证据。

### 9. 构建保留缓存，最终资产统一使用 FaceRec 正式名称

候选阶段允许源码目录临时名 `facerec_v1.3`，但最终 Git 树只保留 `facerec/`。远端构建复用既有 BuildKit layer cache，不使用 `--no-cache`，不执行 builder/buildx/system prune。正式 image repository 保持 `algorithm-facerec`，Compose service 与实例保持 `facerec-gpu0`、`facerec-gpu1`、`facerec-gpu2`；版本只进入 tag、revision label 和证据，不进入项目、service 或 container 基名。

旧容器和镜像在新三实例注册、GPU、真实识别、Gateway 路由和清理前 Smoke 全部通过后，才按预先记录的完整 ID 精确删除。删除后必须重跑 Smoke，并证明模型、卷、BuildKit cache、中间件和无关容器未变化。

### 10. 使用中文规范提交和 Harness 证据推动交付

实现按可验证阶段形成中文 Conventional Commit，提交只包含本 change 拥有的文件并推送到选定集成分支。Harness 新增 `harness/scenarios/facerec-v1-3-upgrade-20260917.md`，同步 `harness/verification.md` 与 `harness/change-ledger.md`；远端原始证据使用 release 目录、`0600`、单硬链接和 write-once 规则，并绑定实际测试 Git SHA 与镜像 revision。文档后续提交不得冒充先前镜像 SHA；需要时以新的 SHA 重建并重新验证。

## 风险与权衡

- [InsightFace 与旧 Dlib 对齐产生的 embedding 分布不同，可能降低旧库命中率] → 使用同一 ArcFace 模型对现有 fixture 和新录入人员执行跨版本识别对比；不自动重写数据库，阈值变化必须有量化证据和单独批准。
- [多人脸内部语义难以完全表达在旧单 `bbox` 响应中] → 保留主脸 `bbox` 与现有字段，人员匹配聚合到既有列表；以契约测试固定该语义，不偷偷增加远端驼峰字段。
- [InsightFace、ONNX Runtime、FastDeploy、NumPy 和 OpenCV 存在严格版本耦合] → 在 Python 3.10 环境锁定经实测组合，执行 `pip check`、导入测试和真实推理，不做无证据升级。
- [两个 GPU 推理后端可能重复占用显存] → 记录启动与推理显存，单 worker 验证后再确定容量；readiness 只有在两类必需模型准备完成后才成功。
- [上游 Redis cache 被误带入后违反平台依赖边界] → 删除算子直连 Redis 的配置、依赖和生命周期，使用静态扫描与远端连接证据确认只有 Control Service 连接平台 Redis。
- [嵌套 Git、LFS 部署 tar 和大模型污染父仓库] → 临时目录不加入父仓库，克隆前检查 Git LFS；最终迁移采用允许清单并运行大文件与 gitlink 检查。
- [最终替换与现有未提交修改冲突] → 替换前后记录精确 `git status`，只处理 `facerec` 和本 change 工件；发现 `facerec` 新的用户修改时停止合并并人工协调。
- [删除旧镜像或容器误伤无关资产] → 先生成包含完整 ID、Compose 身份和 revision 的 dry-run 清单；仅在新三实例验收通过后执行精确删除，禁止 prune 和卷删除，随后复核全平台状态。

## 迁移计划

1. 记录当前父仓库状态、FaceRec 路由/OpenAPI、配置、测试结果和模型校验基线。
2. 克隆并固定 v1.3 提交到 `facerec_v1.3/`，处理 Git LFS 前置但不引入无关部署 tar。
3. 注入完整模型，完成标准项目布局和依赖锁定。
4. 合入平台能力、API 兼容适配、MongoDB 直接读取和严格设备/readiness 行为，删除上游算子直连 Redis 路径。
5. 在临时目录按本地分层门禁验证，并使用中文 Conventional Commit 提交、推送可追溯候选。
6. 在 `192.168.29.11` 记录只读基线，使用保留 BuildKit cache 的候选构建完成三实例平台适配与真实验证；失败时不触碰当前正式 FaceRec。
7. 候选门禁全部通过后，移除旧 `facerec/` 受管内容、剥离临时嵌套 `.git`，把 `facerec_v1.3/` 改名为唯一 `facerec/`，检查父仓库 diff 后提交并推送。
8. 在 `192.168.29.11` 基于最终目录和最终提交 SHA 复用 cache 重建 `algorithm-facerec`，以正式 `facerec-gpu0/1/2` 名称滚动替换并重跑完整平台门禁。
9. 新版本通过后，按 dry-run 清单精确删除旧 FaceRec 容器和镜像，保留缓存、卷、模型和无关资产；清理后再次 Smoke。
10. 补充 Harness 场景、验证命令、变更账本和脱敏证据，执行一致性与 OpenSpec strict 校验；若最终失败，只回退本 change 的 FaceRec 修改。

## 待确认事项

- 生产 GPU 主机上的最终显存占用、单实例并发容量和 `max_concurrent_requests` 是否仍可保持 `128`，必须以 v1.3 实测结果确认；本 change 不在无压测证据时自行调整平台声明容量。

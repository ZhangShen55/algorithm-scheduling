## 1. 冻结基线并建立隔离项目

- [x] 1.1 记录父仓库当前分支、完整 `git status`、`facerec/` 跟踪文件、现有测试结果及与本 change 无关的用户修改，形成仅允许修改 FaceRec 和本 change 工件的基线。
- [x] 1.2 导出当前 `facerec` 的 OpenAPI 路径、方法、请求/响应字段、默认配置和 `/ops/*` 行为，建立升级后的契约对比基线。
- [x] 1.3 核对 `git@github.com:ZhangShen55/FaceRec.git` 的 `v1.3` 是否仍指向 `980e81b7a0bf9bffc07cd5c338a3f91eef1a019d`；若已移动，先记录提交差异并更新 change，不继续静默克隆。
- [x] 1.4 检查或补齐 Git LFS 前置，以不检出无关部署 tar 的方式把固定 v1.3 提交克隆到同级 `facerec_v1.3/`，并确保临时 `.git` 不成为父仓库 gitlink。
- [x] 1.5 建立上游 v1.3 与当前平台版的逐模块差异清单，覆盖推理、路由、配置、缓存、日志、readiness、平台注册、Docker、测试和文档。
- [x] 1.6 核对外置 `ai_models` 的七个必需二进制，将其注入 `facerec_v1.3/ai_models/`，逐文件验证非零和来源一致，并确认父仓库不跟踪模型。

## 2. 整理标准项目结构与依赖

- [x] 2.1 将上游运行源码整理到 `facerec_v1.3/app/`，保留根 `config.toml`、`config.example.toml`、`requirements.txt`、`docker/`、`tests/`、`docs/`、`media/`、`logs/` 和 `ai_models/`。
- [x] 2.2 把跨包导入统一为 `app.` 绝对导入，删除对目录名、临时符号链接、临时 `PYTHONPATH` 和非规范入口的依赖。
- [x] 2.3 合并配置模型，保留 `[platform]`、`[runtime]`、`[gpu].device`、MongoDB、日志和 `save_person_photo` 合同，增加 InsightFace 显式配置，并删除上游 FaceRec 直连 Redis 配置。
- [x] 2.4 从显式项目根解析配置、模型、日志和媒体路径，实现并测试 `CONFIG_PATH` 覆盖。
- [x] 2.5 为 Python 3.10 锁定经验证兼容的 FastDeploy、InsightFace、ONNX Runtime、NumPy、OpenCV、Dlib、FastAPI、Motor 和 PyMongo 依赖组合，并移除未使用的 Redis client。
- [x] 2.6 更新 `.gitignore` 与 `.dockerignore`，排除嵌套 Git、模型二进制、日志、媒体、cache、编译产物和部署 tar，同时保留必要目录占位与模型说明。
- [x] 2.7 更新 Dockerfile、entrypoint 和 Compose 配置，使容器只以 `app.main:app` 启动并创建规定的 `logs/`、`media/` 目录。

## 3. 合入 v1.3 推理能力

- [x] 3.1 在现有进程隔离边界内实现 InsightFace `buffalo_l` worker 初始化、模型文件校验、模型预热、状态采集和受控关闭。
- [x] 3.2 让 InsightFace 与 ArcFace 共用 `[gpu].device`，支持显式 CPU，并在 `require_gpu=true` 时验证两者都使用所选 CUDA 设备且禁止 CPU 回退。
- [x] 3.3 实现 InsightFace 多人脸检测、置信度与最小尺寸过滤、关键点对齐，并保留可配置的 Dlib 兼容检测路径。
- [x] 3.4 排除上游 `points`/ROI 请求扩展，并以 OpenAPI 契约测试固定 `/recognize` 仍只有既有请求字段。
- [x] 3.5 复用 ArcFace 512 维 embedding 和既有候选/全库阈值规则，保留损坏 embedding 过滤及不泄露数据的诊断日志。
- [x] 3.6 将 `/recognize` 改为处理全部有效人脸、按 number 去重并按相似度排序，同时用主脸 `bbox` 和既有 `match` 字段保持当前响应合同。
- [x] 3.7 保持 `/recognize/batch` 的单人多帧聚合语义，验证它不受单图多人脸实现影响。
- [x] 3.8 增加有脸、无人脸、多人脸、尺寸过小、命中、未命中和 batch 聚合的单元与契约测试。

## 4. 保持人员存储与平台依赖边界

- [x] 4.1 移除上游 Redis client、embedding cache、配置和生命周期调用，静态验证 FaceRec 不直接连接平台 Redis。
- [x] 4.2 保持 MongoDB 为人员事实和 embedding 的唯一来源，校验读取候选的类型、维度和有限值。
- [x] 4.3 保持人员新增、批量新增、更新和删除只提交共享 MongoDB，并覆盖重试和部分失败。
- [x] 4.4 验证 `facerec-gpu0/1/2` 在人员新增、更新和删除后通过共享 MongoDB 观察到一致事实，无需算子侧 cache 失效。2026-09-18 在 `192.168.29.11` 的三个隔离候选实例完成真实图片闭环：gpu0 新增、gpu1 更新、gpu2 删除后，三实例均立即观察到相同事实，识别命中率为 `100.00%`，删除后均不再返回验证人员；未执行 Redis cache 失效。
- [x] 4.5 验证升级不会自动删除、重写或批量重算既有 embedding。
- [x] 4.6 保留 `save_person_photo=false` 默认行为，验证单个和批量录入都生成非空 embedding、返回空 `photo_path` 且不写人脸原图。

## 5. 恢复平台运行时能力

- [x] 5.1 移植共享 operator registry client 集成，保持 `operator_code=facerec`、`capabilities=["recognize"]`、注册开关、心跳和容量合同。
- [x] 5.2 保持 Dlib/InsightFace worker 数量与平台 `max_concurrent_requests` 含义独立，并在停止注册前拒绝新租约、等待受控关闭。
- [x] 5.3 扩展 readiness，分别覆盖 MongoDB、所选检测器 worker、ArcFace 和严格设备状态；平台注册与租约只通过 Control Service，不增加 Redis readiness 或直连探针。
- [x] 5.4 恢复共享 JSON Lines 日志、100 MiB 轮转、七天保留、实例目录和 handler 去重，并扫描日志确认不包含 Base64、完整请求/响应、凭据、人员信息或 embedding。
- [x] 5.5 恢复项目根运行目录创建、MongoDB 环境覆盖、请求 ID、中间件、静态资源和媒体路径边界。
- [x] 5.6 更新 `/ops/health`、readiness、metrics 和 stats，使模型、设备、MongoDB 与平台注册状态可验证且不改变既有响应字段。
- [x] 5.7 更新 `README.md`、Docker 文档、模型说明和运维命令，记录标准入口、配置、模型布局、MongoDB 事实源、禁止算子直连 Redis、CPU 本地验证和严格 GPU 部署方式。

## 6. 在隔离项目执行验证门禁

- [x] 6.1 在 `facerecapi` 环境执行 `python -m compileall -q app`、`from app.main import app`、关键依赖导入和 `pip check`，修复所有错误与 ABI 冲突。
- [x] 6.2 运行 `facerec_v1.3/tests` 全量测试，确认单元、契约、配置、日志、设备、readiness、人员存储、Redis 边界和运行路径测试全部通过。
- [x] 6.3 执行缺模型、坏模型、非法配置、CUDA provider 缺失、GPU 索引越界和 worker 启动失败等负向测试，确认严格模式不会宣告 ready 或回退 CPU。
- [x] 6.4 启动 `python -m uvicorn app.main:app --host 127.0.0.1 --port 8003 --workers 1`，检查 health、readiness、OpenAPI、优雅关闭和无临时 `PYTHONPATH`。
- [x] 6.5 使用测试 MongoDB 执行人员新增、批量新增、查询、搜索、删除和跨实例一致性集成测试，并确认 FaceRec 没有 Redis 连接。
- [x] 6.6 使用 `tests/data/常泽宇.png` 完成录入与识别真实闭环，验证 512 维 embedding、命中结果和 `save_person_photo=false` 不落图。
- [x] 6.7 使用可重复多人脸 fixture 完成整图真实推理，验证至少两张有效人脸进入流程且响应仍符合旧合同。
- [x] 6.8 对比升级前后的 OpenAPI 和代表性响应，确认路径、方法、请求字段、`status_code`、`has_face`、`bbox`、`match` 和业务状态码兼容。
- [x] 6.9 构建并检查本地 Docker 镜像与构建上下文，确认入口、运行目录、模型交付及敏感/临时资产排除正确；该结果不替代指定服务器平台验收。2026-09-18 在 Docker Desktop `29.5.3` 以 `linux/amd64` 构建 `algorithm-facerec:local-9a455a1`，镜像 ID 为 `sha256:374d59e0c0eaf06bd6ba03a883cbe11afb01b628ea5e5b91eb5b95fce683d13d`，revision 为 `9a455a1707d4cab1b19179c5a31142b84872f83a`；镜像内 `pip check`、七模型、入口、运行目录和允许清单检查通过。
- [x] 6.10 使用中文 Conventional Commit 提交并推送隔离候选拥有的代码、测试和文档，记录候选分支与完整 Git SHA，不混入其他用户修改。候选分支为 `codex/upgrade-facerec-to-v1-3-candidate`，远端提交为 `9a455a1707d4cab1b19179c5a31142b84872f83a`。
- [x] 6.11 在 `192.168.29.11` 使用已批准固定登录合同执行只读 preflight，记录平台、三张 GPU、FaceRec 旧容器/镜像完整 ID、端口、磁盘和 BuildKit cache 基线，凭据不进入证据。2026-09-17 基线：三张 GPU 可见，三实例端口 `18003/28003/38003` 均健康，活动镜像 `sha256:49352c58c2a3cc39b3c37a52d7d6dbb8951de3db45c5661835a9a2dbc689d5cd`，根盘可用约 181 GiB，BuildKit cache 约 96.72 GiB。
- [x] 6.12 在 `192.168.29.11` 不使用 `--no-cache`、不执行任何 prune，复用并保留 BuildKit cache 构建候选 `algorithm-facerec` 镜像，记录构建前后 cache、镜像 ID 和 revision。2026-09-17 候选 `algorithm-facerec:candidate-9a455a1` 构建返回 `BUILD_RC=0`，镜像 ID 为 `sha256:5d95e67096b1811cabba6a2393a96420e3d4b3b94cc63f53b0d5c71429cbf355`，revision 为 `9a455a1707d4cab1b19179c5a31142b84872f83a`，镜像内 `pip check` 通过且七个模型均非空；构建后 BuildKit cache 仍为 `96.72GB`。
- [x] 6.13 在 `192.168.29.11` 以 `require_gpu=true` 隔离启动候选，执行 health/readiness 和真实识别，记录两个推理后端的 provider、GPU 设备、显存、延迟和无 CPU 回退证据。2026-09-18 候选以端口 `18013/28013/38013` 隔离启动，三实例均为 `healthy`、`model_ready=true`；ArcFace 为 FastDeploy GPU，InsightFace 五个 session 的首选 provider 均为 `CUDAExecutionProvider`，GPU 0/1/2 均存在对应主进程与 detector worker，真实识别约 `85-124 ms` 且无 CPU 回退。
- [x] 6.14 在 `192.168.29.11` 验证候选的平台配置、注册、心跳、声明容量、真实租约调用、Online Gateway 路由、共享 MongoDB、日志和停止顺序；无实测证据时不调整 `max_concurrent_requests=128`。2026-09-18 三实例通过隔离 Control Service 注册并持续心跳，均声明容量 `128`；真实租约识别和隔离 Online Gateway 路由成功，共享 MongoDB 闭环与 JSON Lines 脱敏扫描通过。gpu2 先进入 `DRAINING` 拒绝新请求，再正常注销并停止，重启后重新注册为 `ONLINE`，因此保留容量 `128`。
- [x] 6.15 汇总每个本地与远端候选门禁的命令、环境、实际 Git SHA、结果和脱敏摘要；存在任何未解释失败时保持当前正式 `facerec/` 与旧远端资产不变。完整记录见 `candidate-verification.md`；QEMU dlib 链接、Docker credential helper、正式 Control origin 拒绝和人工 DELETE 方法修正均已解释并闭环，正式 `facerec/` 与旧远端资产在候选阶段保持不变。

## 7. 正式目录改名与平台最终替换

- [x] 7.1 替换前重新检查父仓库状态和 `facerec/` 精确差异；发现新的非本 change 用户修改时停止自动替换并先保留、协调这些修改。2026-09-18 复核确认正式 `facerec/` 无 tracked 或 untracked 差异，候选工作树干净，真实 fixture SHA-256 与旧目录一致，七模型与外置来源逐文件一致；无关用户修改清单未发生漂移。
- [x] 7.2 候选全部通过后移除旧 `facerec/` 受管内容，剥离 `facerec_v1.3/.git`，将 `facerec_v1.3/` 改名为唯一正式 `facerec/`；不保留带版本号的最终项目目录。2026-09-18 已将旧正式目录和候选嵌套元数据移动到工作区外的可恢复临时备份，并把候选原子改名为唯一 `facerec/`；工作区不再存在 `facerec_v1.3/`、嵌套 `.git` 或 `.msc`。
- [x] 7.3 使用允许清单复核最终 `facerec/`，排除模型二进制、日志、媒体、cache、编译产物、部署 tar 和凭据，并确认入口仍为 `app.main:app`。已移除候选测试生成的唯一运行日志；最终目录不存在嵌套 Git、`.msc`、部署 tar、凭据或非占位运行数据，且标准入口检查通过。
- [x] 7.4 将完整外置模型注入最终 `facerec/ai_models/` 并重新验证字节一致，同时确认模型仍被父仓库忽略。七个模型与外置来源的聚合 SHA-256 均为 `a3c1441801e0c556db9a88f919b83cb5f7f8177acfad1a9589dbe063c274adc8`，父仓库状态不包含模型二进制。
- [x] 7.5 更新受影响的平台 Compose、构建清单、验证脚本和路径文档，保持 `algorithm-facerec`、`facerec-gpu0/1/2`、默认端口、平台注册和 Online Gateway 路由合同，不残留活动 `facerec_v1.3` 名称。平台 GPU 配置已显式选择 InsightFace `buffalo_l` 和单 detector worker，模型资产清单补齐五个 ONNX；现有 Compose、镜像 repository、三实例、端口和 Gateway 路由名称无需改名，相关聚焦回归 `64 passed`。
- [x] 7.6 检查父仓库 diff，确认最终 FaceRec 是普通受管文件、不含 gitlink或大模型，且 ASR Online、Text Analysis 与其他无关修改未被改写。暂存对象审计无 `160000` mode、无大模型或禁止资产，`git diff --cached --check` 通过；用户的 `.gitignore`、ASR Online、Text Analysis 和另一 change 均保持未暂存。
- [x] 7.7 在最终 `facerec/` 路径重新执行 compileall、导入、`pip check`、全量测试、uvicorn、health/readiness、真实单人/多人推理和 OpenAPI 对比。2026-09-18 最终路径 compileall、导入和 `pip check` 通过，全量为 `81 passed, 2 skipped, 1 warning in 39.49s`，显式真实单人/多人推理为 `1 passed in 13.48s`；Uvicorn 正常预热并关闭，ArcFace/InsightFace worker 为 `up`，本机未运行 MongoDB 时 readiness 按合同为 false，OpenAPI 仍为 `POST /recognize`、请求字段 `photo/targets/threshold`、响应字段 `data/message/status_code`。本轮 116 行日志全部为 JSON Lines 且脱敏扫描通过，生成日志与 cache 已清理。
- [x] 7.8 使用中文 Conventional Commit 提交并推送最终目录替换、平台定义与验证代码，记录远端分支和完整提交 SHA。中文 Conventional Commit 为 `feat(facerec): 升级并替换为 v1.3 多人脸版本`，已推送至 `origin/codex/milestone-2b-three-gpu-deployment`，完整提交为 `37d8e1ecb0972e0e7c803c4a3277a0738b3f2a36`。
- [x] 7.9 在 `192.168.29.11` 拉取最终提交，保留 BuildKit cache 重建 `algorithm-facerec`，以正式 `facerec-gpu0/1/2` 滚动替换并验证三实例 GPU、health/readiness、注册和共享 MongoDB。2026-09-18 最终提交 `37d8e1ecb0972e0e7c803c4a3277a0738b3f2a36` 构建为 `algorithm-facerec:v1.3_37d8e1e`，镜像 ID `sha256:68e3f880d7ec340095f899ad41e7dd8e9c53827d42b8a5417b3f7e355a7cc88c`；三实例按 gpu0、gpu1、gpu2 滚动替换后均为 healthy、`ONLINE`、`model_ready=true`、容量 `128`，并分别绑定物理 GPU 0/1/2，ArcFace 与 InsightFace 均使用 CUDA。构建后 BuildKit cache 由基线 `96.72GB` 增至 `109.2GB`，未禁用或清理缓存。
- [x] 7.10 通过 Control Service 与 Online Gateway 执行真实租约识别、管理接口、日志脱敏、`save_person_photo=false` 和清理前 Smoke，确认最终命名与平台合同无误。正式 Gateway 完成人员录入、查询、真实租约识别和删除，录入返回空 `photo_path`，三实例真实图片识别均命中且删除后共享 MongoDB 均不可再查；清理前 Control Service、Online Gateway 和三实例 Smoke 通过，正式日志敏感模式命中为 0、JSON Lines 校验通过且三个容器媒体文件数均为 0。
- [x] 7.11 生成旧 FaceRec 容器/镜像完整 ID、Compose 身份、revision 和引用状态的精确清理 dry-run；现场身份漂移或新版本任一门禁失败时禁止删除。dry-run 确认旧容器完整 ID 为 `bcafafb42cc07f50e6c11ff4b00992de733c4a161129a81464ccab04af56d84e`、`047d4ce7edb8bdad04cc94022f13974b3cef879b1d55e10402c5540d7371a685`、`ad57a8176305be66ffadf05b3b4301eabb4f4b8431f549a5ea9b6652505ff860`，均属于 `algorithm-operators` 的 `facerec-gpu0/1/2` 且只引用旧镜像 `sha256:49352c58c2a3cc39b3c37a52d7d6dbb8951de3db45c5661835a9a2dbc689d5cd`（revision `d19e5e46b9cb0c78d775727e1cf33a75a4321df8`），现场身份无漂移。
- [x] 7.12 审核通过后只删除 dry-run 中被替换的旧 FaceRec 容器和镜像，不删除缓存、卷、模型、中间件、其他算子或无关镜像，并执行清理后 Smoke。三个旧正式容器在滚动阶段按完整 ID 停止并删除，旧正式镜像及无引用的隔离候选镜像已按完整 ID 删除；隔离候选五个容器也按完整 ID 删除。清理后卷清单摘要和无关容器清单摘要与基线一致，BuildKit cache 为 `109.2GB`，三实例、Control Service、Online Gateway 和真实租约识别再次通过。
- [x] 7.13 若最终路径或远端平台任一门禁失败，只回退本 change 对 FaceRec 的精确修改并保留失败证据，不执行仓库级 reset/clean，不触碰其他项目修改。本次最终门禁全部通过，未触发回退；全程未执行仓库级 reset/clean、Docker prune、卷删除或无关项目修改。

## 8. 清理与完成证据

- [x] 8.1 确认改名后不存在 `facerec_v1.3/` 或嵌套 Git 元数据，工作区只保留最终 `facerec/`、本 change 工件和用户原有修改，不删除或修改外置模型来源。2026-09-18 复核无 `facerec_v1.3/`、嵌套 `.git` 或 `.msc`，父仓库不跟踪模型二进制；最终目录七模型与 `/Volumes/Data55/算子功能部署/facerec/ai_models` 逐文件一致，外置来源保持七个模型且未被修改。
- [x] 8.2 新增 `algorithm-scheduling-platform/harness/scenarios/facerec-v1-3-upgrade-20260917.md`，记录本地层级和 `192.168.29.11` 远端平台、构建缓存、三实例、真实租约与清理前后证据。场景已记录最终 SHA、镜像与新旧容器完整 ID、三卡 CUDA、注册、Gateway 管理/租约、日志、无落图、dry-run、精确清理、缓存保留、命令修正和证据更正链。
- [x] 8.3 同步 `algorithm-scheduling-platform/harness/verification.md` 和 `harness/change-ledger.md`，明确实际测试 Git SHA、镜像 revision、验证层级、遗留限制和登录凭据未进入普通证据。两份 Harness 索引已绑定最终实现 SHA `37d8e1ecb0972e0e7c803c4a3277a0738b3f2a36` 与镜像 revision，并明确本次未新增峰值压测、历史未引用回滚镜像不在清理范围；新增文档和远端证据的凭据/Base64 扫描通过。
- [x] 8.4 将远端原始证据按 release/SHA 写入受控报告目录，使用 `0600`、单硬链接、write-once 和脱敏检查，禁止改写历史 release。证据根为 `/root/workspace/algorithm-scheduling-facerec-37d8e1e/algorithm-scheduling-platform/deploy/reports/facerec-v1-3-upgrade-20260917/releases/37d8e1ecb0972e0e7c803c4a3277a0738b3f2a36`；所有文件权限 `0600`、硬链接数 1，主 manifest 和 supplemental manifest 校验通过，脱敏扫描通过。首份推理证据的 fixture SHA 手工转录错误未被改写，已由独立更正文件和重新执行的权威推理证据透明纠正。
- [x] 8.5 确认远端只保留最终正式 FaceRec 活动资产和必要回滚策略，不存在活动 `facerec_v1.3` 容器、service、repository 或项目目录，BuildKit cache 保持存在。活动资产仅为 `algorithm-facerec:v1.3_37d8e1e` 和正式 `facerec-gpu0/1/2`；隔离候选五个容器、候选目录、临时 Git bundle 和 wheel HTTP 服务已精确清理，wheel cache、最终 release checkout、历史未引用回滚镜像和 BuildKit cache 保留。
- [x] 8.6 在 `tasks.md` 逐项登记实际完成状态，并保存提交基线、验证日期、测试汇总、路由兼容结论和已知资源容量限制。已逐项登记 2026-09-18 的本地与远端结果、提交与镜像 SHA、OpenAPI 兼容、容量 `128` 保留依据，并明确未执行新的峰值吞吐压测。
- [x] 8.7 使用中文 Conventional Commit 提交并推送 Harness 与 OpenSpec 收口记录，提交不得包含密码、模型、人脸原图或其他用户修改。中文 Conventional Commit 为 `docs(harness): 收口 FaceRec v1.3 远端验收证据`，已推送至 `origin/codex/milestone-2b-three-gpu-deployment`，完整提交为 `edec3d419cb7a6fbc2e7ce5056835a012243efb6`；暂存范围与脱敏扫描均通过，仅包含本 change 的四个 Harness/OpenSpec 文件。
- [x] 8.8 运行 Harness consistency、相关平台回归、OpenSpec strict 和 `git diff --check`，确认所有工件、Requirement、Scenario、证据引用和任务均完整可追溯。2026-09-18 最终结果为 Harness consistency `5 passed`、平台 FaceRec/部署/日志/模型/布局聚焦回归 `72 passed`、`openspec validate upgrade-facerec-to-v1-3 --strict` valid、`git diff --check` 零输出，新增 Harness/OpenSpec 脱敏扫描通过。

## 9. GPU 进程命名修正

- [x] 9.1 在 `192.168.29.11` 以 PID、PPID、完整命令行、cgroup 和容器完整 ID 核对 `3551110` 与 `3551304` 的归属，确认二者分别是 `facerec-gpu0` 的 ArcFace 主进程和 InsightFace spawn worker；不规范路径来自 worker 继承的绝对 `sys.executable`。
- [x] 9.2 移除 `/run/operator-python/facerec` 软链接方案，让容器入口同时作为 `multiprocessing` spawn executable，以 `exec -a facerec` 统一主进程和检测 worker 的 `argv[0]`，且继续保持 spawn 隔离。入口脚本不再创建 `/run/operator-python`，主进程和 spawn 子进程均通过同一可执行入口设置短 `argv[0]`；应用仅接受可执行的绝对 spawn 入口路径。
- [x] 9.3 增加入口包装器真实 spawn 测试及静态合同测试，运行 FaceRec 全量测试、平台入口聚焦回归、compileall、导入、依赖检查、OpenSpec strict 和 `git diff --check`。macOS 结果为 FaceRec `81 passed, 2 skipped, 1 warning`、平台入口与 Harness consistency `14 passed, 1 skipped`，compileall、导入、`pip check`、OpenSpec strict 和 `git diff --check` 均通过；目标 Linux 首次动态探针因只读取到 shebang `/usr/bin/env` 过渡态而失败，改为有界轮询后真实执行 `10 passed`，最终 spawn `argv[0]` 为 `facerec`。
- [x] 9.4 使用中文 Conventional Commit 提交并推送进程命名实现，不混入 ASR Online、Text Analysis 或其他用户修改。实现提交为 `48070e6a12d84f1f716a1d8b24fa9af22e844ee1`（`fix(facerec): 统一 GPU 子进程名称`），测试修正为 `cc3c78dbcd608f77461d7d39bc5c5c6d156d6276`（`test(facerec): 稳定校验 spawn 进程名称`），均已推送至 `origin/codex/milestone-2b-three-gpu-deployment`。
- [x] 9.5 在 `192.168.29.11` 拉取实现提交，不禁用或清理 BuildKit cache，重建正式 `algorithm-facerec` 镜像并滚动替换 `facerec-gpu0/1/2`。最终镜像 `algorithm-facerec:v1.3_cc3c78d` 为 `sha256:482f4c48fb3b511b165f17609df5d3199f95c1e127048d772b80ffb59daca119`；第一次构建遗漏 `--network host` 未产出镜像，修正后依赖层命中 cache。新容器完整 ID 为 `f5dd3e...111fd1`、`426fa6...31d3b`、`fe96de...d10aa4`，均 healthy。
- [x] 9.6 对三实例分别执行 health/readiness、平台 ONLINE、真实识别和宿主 GPU PID 取证，确认所有 FaceRec CUDA 进程名均精确为 `facerec`，且 PID/cgroup 映射正确。三实例均为 ONLINE、`model_ready=true`、容量 128；主/worker PID 分别为 `2886129/2886313`、`2894314/2894619`、`2902643/2902899`，六行 `nvidia-smi` 名称均为 `facerec` 并映射到对应容器完整 cgroup。三实例直连、共享 MongoDB `INF-FACEREC` 闭环和 Gateway 租约识别通过。
- [x] 9.7 新实例全部通过后按完整 ID 精确删除本次被替换的旧 FaceRec 容器和镜像，保留 BuildKit cache、卷、模型、中间件、其他算子和无关镜像，并执行清理后 Smoke。旧容器 `005213...edd842`、`a50d68...079bf1`、`865ead...7d2539` 已由滚动替换删除，零引用旧镜像 `sha256:68e3f880...5a7cc88c` 已按完整 ID 删除；卷摘要未变，BuildKit private cache 为 `109.9GB`，清理后 health、注册、PID/cgroup 和 Gateway 真实识别再次通过。
- [ ] 9.8 补充 Harness 场景、验证索引、变更账本和远端受控证据，使用中文 Conventional Commit 提交推送，最终运行 Harness consistency、相关平台回归、OpenSpec strict 和 `git diff --check`。

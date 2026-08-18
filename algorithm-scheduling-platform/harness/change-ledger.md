# Change Ledger

## 2026-08-19 - 8A.3 GeForce `pmon` 不可用指标语义修正

- 现场现象：`b8431c0fd4b135db1a8cc34ae4b9cae48e7e0655` 已完成八算子和四平台镜像构建、
  基础设施/四平台健康、24 实例分 profile 注册，18 个 GPU 实例也都进入了真实
  请求触发、CUDA PID 映射和停止/重启流程；但每个 running 证据都因
  `nvidia-smi pmon 数值字段格式异常` 标记失败。FaceRec gpu0/gpu1/gpu2 与其他
  15 个 GPU 实例的失败点完全相同，因此不是 FaceRec 镜像、模型、MongoDB 或宿主
  Conda 环境问题。
- 根因证据：目标服务器上的 RTX 4090 D/RTX 3090 属于 `pmon --help` 所说的
  limited GeForce 支持范围。当前驱动输出头比旧夹具多 `jpg`/`ofa` 列，并把
  暂时不可用的逐进程 `sm/mem/enc/dec` 返回为 `-`。验证器直接执行
  `float("-")` 才是失败源头；它在触发请求、容器 CUDA PID 和显存已经成立后
  才失败。
- 修正：保留 NVIDIA 缺失值语义，将 `-` 写为 JSON `null`，不伪装为 `0%`；
  可用的逐进程数值仍必须在 `0..100` 内。反例校验器只对“明确测得 SM 为 0 且
  CPU 忙” fail closed；对不可用指标依然强制校验真实请求完成、框架 CUDA 探针、
  compute-apps PID/进程名、Docker/cgroup PID 归属、显存和全局 GPU telemetry。
- TDD 证据：新增一条精确复刻现场 `jpg/ofa + '-'` 输出的验证器回归，以及
  FaceRec gpu2 不可用指标的 canonical checker 回归；两者均先在旧实现失败，最小
  修正后相关测试 `83 passed` 与 `499 passed, 3 skipped`，Ruff、两个生产脚本的 strict
  Mypy、compileall 和 `git diff --check` 通过。3 个跳过项仍只是本机缺少显式注册令牌与
  Canonical FaceRec GPU 容器的既有条件。
- 完成边界：`b8431c0` 是失败 release，保持只读。本修正必须进入新 Git SHA/新
  不可变 release，并同时出现 `CODEX_STAGE45_COMPLETE failures=0` 与
  `CODEX_8A3_TERMINAL stage45_failures=0 deployment_status=0`，才允许勾选 `8A.3`。

## 2026-08-19 - 8A.3 GPU 恢复与 deployment 入口修正

- 前一现场轮次：`d651dd73228189e686259c235da93cde7a946e5b` 已经让 18 个 GPU 实例的
  真实请求、停止、CUDA PID 消失、重启和重新 `ONLINE` 都实际执行；PPT Slice/
  Text Analysis 六个 CPU 实例及八算子 full Smoke 也没有产生新业务失败。
- 根因 1：Linux 触发器后代只剩 zombie 时，`killpg(..., 0)` 仍认为进程组存在，
  验证器因此把 18 个已回收的触发器统一误报为失败。修正后从 `/proc/<pid>/stat`
  读取状态，只把非 `Z` 成员视为存活；Linux 容器回归明确取得
  `killpg_reports_exists=true` 且 `live_members=false`。
- 根因 2：deployment batch 以文件路径执行，导致 `ModuleNotFoundError: No module named
  'scripts'`。入口已改为 `.venv/bin/python -m scripts.run_milestone_2b_case_batch`，并有专门
  回归防止退回文件路径执行。
- 证据与边界：修正分别在 `6dedca2` 和 `b8431c0` 提交，本地完整回归、Ruff、
  strict Mypy、compileall 和 OpenSpec strict 均通过。这只关闭两个 Harness blocker，不代表
  `8A.3` 远程终态已通过。

## 2026-08-19 - 8A.3 Canonical runner 标准输入截断修正

- 现场现象：`a78c64b187d90d2f0cfd7ecb66c72453661ab652` 两次完成八算子镜像、四平台镜像和
  runtime preflight 后都以状态 0 退出，但没有启动 gpu0 profile，也没有产生
  `CODEX_STAGE45_COMPLETE` 或 `CODEX_8A3_TERMINAL`。
- 根因：原临时 `run-8a3-71e09f1.py` 使用 `subprocess.run(["bash"], input=runtime)`，把控制脚本和
  子进程 stdin 放在同一个管道。`preflight runtime` 内部命令读取继承 stdin 后，尚未被 Bash
  解析的 profile、GPU、deployment 和恢复阶段被消费，因此产生“成功提前结束”。这与 FaceRec
  镜像、GPU、Conda 或算子实现无关。
- 修正：将控制器与 stage45 固化到受版本控制的 `deploy/scripts/`；新增 `execute_runtime`，使用
  `bash -c <runtime>` 隔离控制程序与子进程 stdin。主入口只改用该执行函数，不修改 canonical
  阶段内容和维护边界。
- TDD 证据：新增真实子进程回归，先因 `execute_runtime` 缺失失败；最小实现后，主动读取 stdin
  到 EOF 的子进程不再吞掉其后的 `CODEX_RUNTIME_CONTINUED` 标记。
- 完成边界：本条只关闭控制器截断 blocker。修正提交并进入新的 Git SHA/release 后，必须实际
  取得 FaceRec 三实例、18 个 GPU 实例、全部 deployment 用例、清理和恢复终态，才勾选 8A.3。

## 2026-08-19 - 8A.3 空暂停账本续跑边界修正

- 现场失败：`71e09f10da17a6ad087680b1d5d89e9d5ab431da` 已完成八算子镜像、四平台镜像和
  runtime preflight，但远程会话在 gpu0 profile 前中断；同 SHA 续跑被
  `active maintenance paused ledger must contain exactly one stopped entry` 拒绝。
- 根因证据：本轮 snapshot 中获准维护的原 `ocr-v6-amd` 本来就是 exited，因此 pause 脚本
  正确发布了空 paused ledger；active resolver 却无条件要求唯一 `stopped` 记录，与 completed
  authority 已支持的“原本非 running 时空 audit”边界不一致。FaceRec 镜像、GPU、Conda 和注册
  心跳均不是这次续跑失败原因。
- 修正：active/reuse-local 校验现在按 snapshot 原始状态分支。原本 running 时仍强制唯一
  `stopped`、hash、policy neutralization 和 exited binding；原本不是 running 时只允许空
  paused，并要求当前 Docker binding 与 snapshot 完全一致。
- 回归证据：新增用例先在旧实现上稳定失败，再由最小实现修复；新合法场景和原有 7 个
  不完整/不可信 active transaction 反例合计 `8 passed`。
- 完成边界：该记录只关闭同 SHA 安全续跑 blocker；修复必须进入新 Git SHA/不可变 release，
  实际完成 FaceRec 三实例、18 个 GPU 实例和全部 deployment 用例后才能勾选 `8A.3`。

## 2026-08-18 - 8A.3 持久生命周期恢复门禁修正

- 现场失败：`c418234c337dfac4f3feaaa984127f206acbdbca` 的八镜像和四个平台服务构建成功，
  GPU0 六容器健康且持续心跳；注册预检仍看到 ASR Offline/Online、FaceRec、VBas 和
  ScreenDet 为 `OFFLINE`，因此 canonical 阶段 3 fail closed。该 release 的失败报告保持只读。
- 根因证据：FaceRec `/ops/health`、`/ops/metadata`、心跳和 control 反向探测均为 HTTP 200，
  `model_ready=true`；PostgreSQL `operator_instances.desired_state` 仍保存前次维护设置的
  `DRAINING/OFFLINE`，重新注册按设计不覆盖持久运维意图。OCR 仍为 `ONLINE`，进一步排除
  Docker GPU、模型初始化、注册令牌和全局网络故障。
- 修正：新增 `activate-operator-instances`，只允许从权威 24 实例 Compose 选择一个 profile
  或显式实例，通过鉴权 lifecycle API 恢复 `ONLINE`；canonical profile 启动必须先成功发布
  new ledger，再激活所选实例。GPU stop/restart 取得停止证据后也必须显式激活当前实例。
- 安全边界：激活器不接受任意实例 ID、不清理 PostgreSQL/Redis、不自动覆盖所有实例，
  鉴权失败立即中止，未注册或短暂连接失败仅在有界截止时间内重试。Compose partial-up 或
  ledger 刷新失败时不会执行激活。
- 变更文件：激活器及入口、canonical 部署场景、部署 README、单机运维手册和 Task 9 回归。
- 当前结论：本条关闭重跑前的生命周期门禁缺口，不代表 `8A.3` 已通过。修正必须进入新的
  Git SHA 和不可变 release，并重新执行 FaceRec 三实例、18 个 GPU 实例和 deployment 用例。

## 2026-08-18 - 8A.3 已恢复前驱的跨 SHA 维护事务

- 根因：`7efac20cf980ee64ea78fe297af6dfdfb2df5b28` 已成功执行 restore，canonical
  paused ledger 被归档为唯一 `0400` audit；旧 resolver 仍要求 snapshot/paused 成对存在，
  因而把合法完成态误判为 partial。该问题与 FaceRec 算法和宿主 Conda 无关。
- 实现内容：新增 `fresh-after-restored-previous`。resolver 严格验证前驱 `0600` 单链接
  snapshot、唯一 `0400` 单链接 audit、无 archive metadata、终态记录及 `ocr-v6-amd`
  当前容器绑定/状态；通过后只在当前新 SHA 创建 snapshot/paused。已有平台端口继续由权威
  Compose 与 Docker inspect 派生，算子 baseline/new 仍从立即前驱只读继承。
- 反例：可写 audit、符号链接、额外硬链接、多个 audit、active 状态和残留 metadata 均在
  snapshot/pause 前拒绝；当前 release 自己已经 restore 时要求换新 SHA，禁止覆盖同一不可变报告。
- 变更文件：`deploy/scripts/operator_lifecycle.py`、canonical 部署场景、平台 `AGENTS.md`、
  活动 OpenSpec 设计/部署规格、生命周期测试和本 Harness。
- 本地证据：主成功用例修复前稳定失败为 `maintenance snapshot/paused ledger state is partial`；
  修复后成功用例与相应反例全部通过。完整 `test_milestone_2b_task9.py` 为
  `239 passed`；Ruff、strict Mypy 和 `git diff --check` 通过。
- 远程只读证据：新 resolver 对目标服务器旧 release 的 52 行真实 snapshot、唯一空 audit
  和当前 `ocr-v6-amd` 容器返回状态 `completed`；未改写旧 release、Docker 或报告。
- 完成边界：本条只关闭 8A.3 正式执行前的维护状态机 blocker。FaceRec 三实例、18 个 GPU
  实例和 deployment phase 仍必须在提交后的新 Git SHA/不可变 release 中实际重跑，成功前
  不勾选 OpenSpec `8A.3`。

## 2026-08-18 - 8A.2 真实执行证据与基础执行器闭环

- 先前状态：243 条用例已有稳定目录和真实执行报告合同，但 case runner 的超时回收、
  资源归属、部分注册/GPU/基础设施决定性事实和管理面鉴权尚未全部 fail closed。
- 实现内容：实装安全有界的子进程组监督、超时 TERM/KILL 和后代清理、write-once
  执行证据、按 run/case 隔离的清理合同，以及 DEP/GPU/REG/INF 76 条显式 checker。
  INF-008~012 使用隔离 `_test` PostgreSQL、真实 Kafka topic/group、生产 Repository 和
  adapter；REG-014 从 PostgreSQL 恢复持久 DRAINING；GPU-015 绑定容器内 CUDA runtime、
  3090 身份和真实活动证据。
- 信任边界：Canonical Compose 不再使用仓库已知的注册令牌，必须显式传入
  `OPERATOR_REGISTRY_TOKEN`；host preflight 在 Docker 操作前拒绝缺失值和
  `local-development-registry-token`。`/ops/operator-instances/{instance_id}/drain`
  与其他生命周期写接口使用同一常量时间令牌鉴权。
- 决定性事实修正：INF-001 使用保留但未监听的本机端口建立真实 SQLAlchemy engine，
  分别通过生产 `ControlReadinessChecker` 和 `CourseRepository`/`OrchestratorRuntime`
  观察 PostgreSQL 连接失败。INF-014/015 不再依赖宿主 Conda，而是通过 Canonical
  Compose 唯一解析 `facerec-gpu0`，在容器中使用 scenario 隔离 MongoDB、错误凭据、
  生产人物持久化、候选查询和识别接线；正确管理凭据只用于零写入复查和当前隔离库清理。
  FaceRec 探针使用唯一显式结果帧，允许普通运行日志，但拒绝零帧、多帧、畸形 JSON、
  非对象及 `NaN`/`Infinity`。PostgreSQL 与 MongoDB 资源类型和清理后端严格分离。
- Clean-clone 合同：`aiokafka` 是正式 case runner 的基础依赖，已从 optional extra 移入
  平台基础依赖；发布前的 Python 证据同时验证 `httpx`、PyYAML、`websockets` 和
  `aiokafka`。八个算子继续使用独立构建的轻量注册客户端 wheel，不继承平台依赖。
- 验证证据：Foundation runner `498 passed, 3 skipped`，case runner `96 passed`，
  Harness consistency 与真实 PostgreSQL/Kafka 基础设施用例合计 `10 passed`；Ruff、
  严格 Mypy（本轮 4 个 runner 文件及配置内 26 个公共包文件）、compileall 和
  `git diff --check` 通过。3 个跳过项仅因本机没有显式注册令牌和 Canonical FaceRec
  GPU 容器；生产 checker 在同样条件下仍 fail closed。规格符合性与代码质量复审最终
  均为 `APPROVED`。
- 执行要求：正式 case batch 必须使用 `--require-cleanup`，并保留失败证据。
  `REG-017` 仍标记 `verification_scope=component-level` 和
  `running_e2e_validated=false`，不冒充常驻服务 E2E。
- 完成边界：本条只关闭 OpenSpec `8A.2`。未在远程三卡服务器的新不可变
  release 上重跑 FaceRec 三实例、18 个 GPU 实例或 deployment phase；这些仍属于
  `8A.3`。当前也没有完成 PPT/ASR/视觉/在线业务泳道或 243 条最终总验收。

## 2026-08-18 - 里程碑 2B 真实业务泳道与完整验收分期

- 先前状态：三卡现场报告为 83 通过、6 失败、243 条“未执行及原因”；FaceRec 三实例因
  Harness 调用了镜像中不存在的 `python` 而缺少 GPU 证据。八类算子已有直接 Smoke，但
  PPT、ASR、视觉和在线尚未形成平台入口驱动的真实业务泳道。
- 目标状态：依次关闭 FaceRec 证据、PPT/ASR、视觉、在线和 243 条总验收。最终必须实际
  执行全部 217 条反例和 26 条压力用例，不允许用直接算子 Smoke、Repository 改状态或
  “未执行及原因”替代完整验收。
- 变更文件：阶段设计规格、活动 OpenSpec 设计、Harness 场景、证据矩阵和本台账。
- 契约影响：A 面字段、四种任务类型、整数状态、算子 HTTP/WebSocket 路径和服务边界不变；
  本次只确定实施依赖、证据归属、失败继续规则和完成门槛。
- 证据边界：本记录是设计证据，不是运行通过。旧 release 保持只读；FaceRec 和四条泳道
  必须在后续新 Git SHA、新 release 目录中产生真实证据。
- OpenSpec：继续使用 `close-platform-runtime-and-harness-gaps`，不建立重复变更；待实施计划
  将现有 4.7-4.12、5.1-5.7、6.1-6.5、9.1-9.6、10.1/10.3/10.4 映射到五个阶段。
- 剩余风险：真实泳道实现和 243 条用例仍未执行；任何失败都必须保留 Harness 事实，并阻止
  对应阶段及总里程碑标记完成。

## 2026-08-17 - 里程碑 2B 三卡现场验收与失败报告固化

- 执行版本：目标服务器以 release `v1.0_260812` 和部署提交
  `7efac20cf980ee64ea78fe297af6dfdfb2df5b28` 完成阶段 1-6。证据根目录为
  `/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/v1.0_260812/7efac20cf980ee64ea78fe297af6dfdfb2df5b28`。
- 部署与注册：四个平台服务、PostgreSQL、Kafka、Redis 和 MongoDB 全部健康；
  24 个算子实例全部完成启动、注册、首次心跳、`ONLINE` 和 `model_ready=true`。
  验收后只停止本轮 24 个算子容器，原有容器按快照恢复；八个平台/基础设施
  容器继续 healthy，GPU 已完全释放，根分区剩余约 164 GB。
- 真实推理：18 个 GPU 实例均进入逐实例验证流程，15 个通过；FaceRec
  gpu0/gpu1/gpu2 因验收工具在只提供 `/usr/bin/python3` 的镜像内调用 `python`
  而失败。同一镜像改用 `python3` 直接执行 FastDeploy 探针返回
  `framework_gpu_available=true`，因此失败定位为 Harness 解释器合同错误，
  不是 FaceRec 推理、GPU、注册或容量故障。旧 release 的三条运行 FAIL 和三条
  恢复 FAIL 原样保留。
- Smoke 结果：PPT Slice 三实例和 Text Analysis 三实例的 CPU Smoke 为
  `6/6` 通过；ASR Offline、ASR Online、OCR、VBas、FaceRec、ScreenDet、PPT Slice
  和 Text Analysis 八类 full Smoke 为 `8/8` 通过。PPT 使用约 55 分钟、
  454 MB 的真实 P 视频，四次均完成切片、终态回调和 manifest 对账。
  FaceRec full Smoke 完成人物建立、跨实例识别、查询、清理和不保存原图验证。
- 报告工具：`349f4a7673e1cc203661a11c422f30b4408a1073` 修正 FaceRec 探针和
  aggregator 对完整容器 ID 的证据合同；
  `22a2d55f4523785e62cb384fb1a0ee3a6077d25e` 进一步对齐 renderer。两个工具提交
  都晚于部署 SHA，只读处理旧 release 证据，没有改写历史 FAIL。
- 最终结论：权威 cases 共 332 条，83 通过、6 失败、243 条为“未执行及原因”；
  renderer 返回码为 `3`，`overall_status=失败`。`summary/cases.json`、`summary/report.json` 和
  `summary/report.md` 的 SHA-256 分别为 `4e75f1a657096adba74c9766f2ce24e3d1e69224c3ed1fc827e57e1706a9a877`、
  `8670fdc434e7e8ce19be1728743769928d7c8b699c1b1ce0791445b996b79fe7`、
  `0aa03b2a524a38fe78e22e96ef2dab64343c076b343ae689154da2672af0d8ca`。本轮没有 OOM、
  NVIDIA Xid、kernel OOM 或磁盘不足。
- 完成边界：本轮已证明三卡部署、24 实例注册、15 个非 FaceRec GPU 实例的
  运行证据及八类业务直接 Smoke；没有完成 217 条反例和 26 条压力用例，
  也没有贯通 Kafka 驱动的 PPT/ASR/视觉/在线完整业务泳道。后续必须在新 release SHA
  下重跑 FaceRec 三实例 GPU 证据并执行尚未执行的反例/压力用例，才能重新评估
  里程碑 2B 是否通过。

## 2026-08-17 - 跨 SHA 算子账本来源只读回溯

- 根因：维护 authority 与算子 baseline/new 的生命周期不同。立即前驱可能只有合法的
  `0400` maintenance provenance，而最近的完整算子账本对位于更早 release；旧阶段 3
  直接拼接 `PREVIOUS_RELEASE_ROOT/container-maintenance/{baseline,new}`，因此会在合法的
  A（snapshot/paused）→B（完整算子账本+provenance）→C（仅 provenance）→D（当前）链上
  错误停在 C。
- 修复：`operator_lifecycle.py resolve-operator-ledgers` 从立即前驱开始只读解析，遇到最近的
  完整 baseline/new 对即返回；无账本的每个候选必须先通过完整 maintenance 状态机，只有
  唯一合法的 provenance 状态才可沿 `source_release_root` 回溯。provenance 的所有权、`0400`、
  schema、source SHA/root 和 authority 路径继续严格校验；operator ledger partial、maintenance
  snapshot/paused partial、direct+provenance 歧义、环和最终无完整账本祖先均 fail closed，
  不创建、复制或改写任何账本/provenance。
- 阶段 3：canonical 场景改用 resolver 返回的账本路径；账本排序、完整 ID、Docker inspect、
  Compose project/service 身份、`current - resolved baseline == resolved new` 和同目录临时文件
  原子发布门禁保持不变。D 的 maintenance provenance 仍记录立即前驱 C，权威 snapshot/paused
  仍位于 A，不因查找 B 的算子账本而改绑。
- 本地证据：首轮新增成功链路及 mismatch、operator partial、cycle、no-ancestor 五个回归，
  修复前 5 项均按目标原因失败；质量复审再新增深层 maintenance snapshot/paused partial 与
  direct+provenance 歧义两个回归，旧 resolver 均错误返回成功。最终聚焦测试 `7 passed`，
  完整阶段 3 合同回归 `168 passed`，Ruff、`compileall` 和 Git diff 检查通过。该证据没有
  连接远端服务器，也不表示阶段 3、24 实例或真实推理已重新通过。

## 2026-08-16 - 阶段 3 就绪竞态与 ASR Online 运行依赖修复

- 平台启动竞态：提交 `02203a5b25324767b569bff79b065f79b856d1a0` 的阶段 3 首次重建
  四个平台容器后立即运行 runtime preflight，orchestrator 尚处于 `health: starting`，探针收到
  `Connection reset by peer`。四容器随后均达到 healthy，且同一只读 preflight 完整通过，确认
  根因是 Compose 返回与应用就绪之间的瞬态竞态，不是平台持续崩溃。
- 启动合同修复：canonical 场景改用 Compose `--wait` 和默认 180 秒的
  `--wait-timeout`，并校验覆盖值必须是 1 到 3600 之间的整数；只有四个平台服务均满足自身 healthcheck 后才运行
  最终 attestation。平台 README、部署说明、验证说明和恢复手册同步相同命令；runtime preflight
  仍保持单次、失败即拒绝的最终验收语义，不用重试掩盖健康后的抖动。
- ASR Online 失败证据：平台竞态修复后，gpu0 六实例已由当前 revision 镜像替换，其中五个健康；
  `asr-online-gpu0` 因 `ModuleNotFoundError: No module named 'addict'` 重启，profile 门禁最终以
  “缺失实例、注册验证全局超时”失败。gpu1、gpu2 和 cpu 未启动，current baseline/new ledger
  保持 `0/6`，维护锁和本次日志子进程已精确释放。
- 依赖根因与修复：发布矩阵使用普通 `asr_online/docker/Dockerfile`，但只有 Cython Dockerfile
  显式安装 ModelScope pipeline 运行依赖；普通镜像的构建门禁只验证 Torch/CUDA，未导入
  `app.main`。普通镜像现同步固定的 `modelscope==1.16.0`、`addict==2.4.0`、
  `datasets==2.18.0`、`pyarrow==15.0.2`、`pandas==2.2.2`、`sortedcontainers==2.4.0`
  以及 `Pillow`、`libsndfile` 依赖闭包，并在 build 阶段导入应用入口，
  使缺失运行依赖在生成镜像时直接失败。
- 清理边界：仅删除同时满足“dangling、无任何容器引用、未被权威恢复快照引用”的精确旧镜像 ID；
  仍被六个 gpu0 容器引用或被恢复快照引用的镜像全部保留。未执行 `docker system prune`，未删除
  容器、卷或 `/data/result`。
- 当前证据：ASR Online 完整单元测试 `20 passed`，平台阶段 3 部署合同回归 `161 passed`，
  Ruff 和 Git diff 检查通过。这些结果只证明修复代码门禁；新提交的八镜像统一构建、24 实例注册、真实 GPU
  推理和全泳道验收仍待目标服务器重新执行，不能标记为通过。

## 2026-08-15 - 里程碑 2B 唯一部署执行合同（覆盖旧操作规则）

- 覆盖关系：本条只覆盖旧记录中的“所有密码不得进入 Markdown/Git”、部署时重新生成模型
  manifest、验收结束对 platform/infrastructure 执行 `down`、以及内部端口可对外绑定等
  **操作规则**；旧记录描述的当时失败、构建、推理和修复事实继续保留，不追溯改写。
- 登录与配置：目标固定为 `root@192.168.29.11:22`，密码 `kedacom_123`，代码目录
  `/root/workspace/algorithm-scheduling`。本次部署不使用 `.env`；用户已批准 Git 保存部署
  模板、该登录合同和受控服务默认值。该例外不包含 SSH 私钥/Deploy Key、模型解密密钥、
  人脸原图、课程媒体、大型 fixture 或外部可信模型 manifest。
- 暴露面：只允许 `control-service:18100`、`online-gateway-service:18103` 作为 A/远程
  可信内网入口。PostgreSQL `5432`、Kafka `9092`、Redis `6379`、MongoDB `27017`、
  `18101`、`18102` 和全部 24 个算子宿主机端口收紧为 `127.0.0.1`；容器内服务名、
  HTTP/WebSocket 路径、方法、字段和容器端口不变，因此本次绑定收紧不改变业务端口契约。
  Kafka 使用 `EXTERNAL://:9092`/`INTERNAL://:29092`，分别广播
  `EXTERNAL://127.0.0.1:9092`/`INTERNAL://kafka:29092`。
- 模型权威：Git 外已有 `model-assets.manifest.json` 是可信交付基线。部署只运行
  `stage-model-assets` 与 `verify-model-assets`，不运行生成器覆盖基线。OCR 镜像内派生
  manifest 只用于运行校验，不是第二个交付权威；旧记录中生成清单的动作仍是当时事实。
- revision 证明：四个平台镜像构建显式接收完整 `EXPECTED_GIT_SHA`，运行后由
  `preflight runtime --git-sha` 对最终镜像 attestation；gpu0/gpu1/gpu2/cpu 每个 profile
  启动后立即由 `preflight operators --profile ... --git-sha` 验证，24 实例同时 ONLINE 后
  再运行 `preflight operators --full --git-sha`。Smoke 的 `--git-sha` 仅记录报告归属。
- 生命周期：先快照，只按同一 ledger 暂停用户明确允许的原 `ocr-v6-amd`。baseline/current/new
  ID 先写同目录 `mktemp`，校验完整 64 位 ID、`docker inspect .Id`、baseline 排除和
  project/service 后再原子发布 ledger。验收结束只停止这些新增算子，不执行
  `docker rm`；随后恢复 `ocr-v6-amd`，platform/infrastructure 保持运行。禁止 prune、
  `down -v`、删除卷和删除 `/data/result`。算子 Compose 的权威 project 标签是
  `algorithm-operators`，不是
  平台 project `algorithm-scheduling-platform`。旧场景中的 whole-stack down 命令不再适用于 2B。
- partial-up 边界：四个 profile 只通过 `start_operator_profile` 启动。它保留 Compose 退出码，
  无论成功或失败都先刷新 current-baseline 差集；若刷新成功但 `up` 失败，按原码
  返回并由严格模式中止。若账本刷新失败，不发布临时结果，禁止 cleanup；待
  Docker 恢复后基于已发布 baseline 重新刷新。
- 完整验收：每个 GPU 实例完成真实推理证据后执行 stop、`--assert-stopped`、立即 restart，
  只用 `verify-operator-registration --instance` 等待当前实例的首次心跳、ONLINE 和
  `model_ready=true`，不重复会冲突 write-once 报告的 profile preflight。PPT 三实例和
  Text Analysis 三实例先逐实例 Smoke；再确认 FaceRec 三实例同时 running/ONLINE；
  最后且只执行一次八类 full Smoke、反例、压力和恢复。
- PPT Smoke 回调：`19090` 仅是 Harness-only 临时端口。监听与广播地址都从
  `algorithm-platform` Docker bridge gateway 动态取得，不绑定 `0.0.0.0` 或服务器物理网卡；
  每次 PPT Smoke 结束立即关闭监听，不将该端口列为平台北向入口。
- 当前证据边界：本条记录的是代码和文档执行合同收口，不是服务器运行结果。最终 SHA 的
  四平台镜像、24 实例同时 ONLINE、18 个 GPU 活动、全部 Smoke/反例/压力/恢复尚待真实
  执行；OpenSpec 7.4、7.5 继续保持未勾选，不宣称 PPT、ASR、视觉或在线泳道已贯通。

## 2026-08-15 - OCR v6 AMD64 离线镜像与 RTX 3090 参数收敛

- 交付目标：源项目 `main@e7fb26f1a24c75d2a1623a52a9aa379e2e6771da` 整理唯一 Cython
  保护镜像 `ocr:v6_amd`，保存为 `ocr_v6_amd.tar`；算法功能调度同步基于
  `codex/milestone-2b-three-gpu-deployment@cc790b9` 语义合并，不覆盖平台专属运行资产。
- 镜像证据：Linux AMD64 镜像 ID 为
  `sha256:bba69f2ab3f9521c3d5dde8d3f3803a52f673925d3204552738347c8ff3d5abe`；tar
  大小 `12,806,246,400` 字节，SHA-256 为
  `8201d9234eeac95cc993f76d74890f0dbbce4910a018e2db6ba0472790822cd9`。镜像包含 16 个
  核心原生扩展，不含核心源码、编译中间文件、构建工具或正式配置。
- 契约影响：`/ocr/getVersion`、`/ocr/prediction`、响应字段、端口和设备格式不变；正式配置
  继续只读挂载。示例配置回填 `recognition_batch_size = 4`、`max_concurrency = 1`、
  `enable_hpi = false`、公式 batch `1` 和 `box_threshold = 0.5`。
- RTX 3090 证据：在 `192.168.29.11` 的物理 GPU 2 上完成 batch `1/4/8/16` 与客户端
  并发 `1/2/4/8/16` 的 20 组固定矩阵，共 2,000 个计量请求；全部成功且无 5xx，内容摘要
  一致。推荐 batch `4`、客户端并发 `2`，独立复验 `13.468 QPS`、P95 `152.716 ms`。
  公式路径识别 28 个公式，单请求约 `9.806 s`；容器重启前后 OCR 一致。
- 同步与清理：压测工具、配置注释、Linux 部署文档、测试和报告按允许清单同步。目标
  `app.main:app`、operator runtime、`REQUIRE_GPU`、registry wheel、BuildKit secret、正式
  配置、模型和 NPU Dockerfile 均保留。只精确删除 OCR 中间标签，服务器全部
  `algorithm*` 镜像保持不变，未执行全局清理。
- 证据等级与风险：达到静态/单元/契约、完整镜像构建、真实 NVIDIA GPU 推理、固定矩阵压测
  和重启层级。Cython 不是密码学加密；结果基于单张固定 OCR 图片，上线后仍需按真实图片集
  复测吞吐、P95、显存和内容一致性。

## 2026-08-15 - 八镜像构建完成与 Text Analysis 试用版混淆边界

- 下载故障：提交 `e66a07136e9c594cf8fec3d125ff69e48ca4904e` 的真实构建已完成前七个
  镜像，Text Analysis 的 builder/runtime 通过默认 PyPI 并行安装依赖时连续发生连接重置和
  15 秒读取超时，最终把没有取得 `pydantic` 候选报告为 `No matching distribution found`。
  修复为可覆盖的清华 PyPI 索引，并对三处网络 pip 安装统一使用 300 秒超时、10 次重试和
  Wheel 优先；后续真机构建确认 requirements 和 PyArmor 均从该索引成功下载。
- 混淆故障：下载修复后的提交 `f2ef468d02aea4221b79986fb512732fdf8621b0` 已完成前七个
  新 revision 镜像，但未锁版本的 PyArmor 实际解析为 `9.2.6 trial`，在处理
  `app/models/entities.py` 时报告 `out of license`。隔离复验确认 `8.5.12 trial` 默认强度
  仍在同一文件失败；排除该文件后，其余 57 个源码文件可全部混淆，且该文件单独使用
  `--obf-code 0` 可成功生成混淆产物。
- 修复边界：Text Analysis 锁定 `PyArmor 8.5.12`；递归阶段只排除超限的
  `app/models/entities.py`，随后以 `--obf-code 0` 单独处理并将产物安装回统一的
  `/build/obf`。运行镜像仍只复制混淆目录，不复制明文 `entities.py`。这一个数据模型文件
  关闭函数级混淆，保护强度低于其余源码；后续若提供商业许可证，应恢复全量默认强度。
- 最终证据：提交 `e65dd576b3b53b73a874bb131449ef031423057b` 在 x86_64 目标服务器
  完成统一 `build-images`，八个 `v1.0_260812` 镜像逐一 inspect，OCI revision 全部精确匹配，
  日志终态为 `PASS: eight images built and inspected`。Text Analysis 成品镜像可导入
  `app.main:app` 和 `ModelCard`，容器内产物不含明文 `class ModelCard`；实际启动后
  `GET /openapi.json` 返回 HTTP 200。构建期间根盘最低值未越过 100 GiB 门禁，无 OOM。
- 证据范围：本记录完成阶段 2 的八镜像构建和 Text Analysis 启动 Smoke；24 实例拓扑、
  三卡 GPU 真实性、注册、八类真实推理、反例、压力、恢复及完整泳道仍属于后续阶段。

## 2026-08-14 - ASR Offline 运行配置收敛与固定日志

- 决策：`/get_status` 的 `appVersion` 改为代码常量 `asr:latest`，不再从 TOML 读取；保留
  `id_engine` 及状态接口字段，不改变 HTTP 契约。
- 设备语义：移除 `ngpu` 和 `ncpu` 配置。Paraformer/emotion2vec 的 `ngpu` 在模型加载时
  根据已校验的 `device` 推导：`cpu` 为 `0`，`cuda:<index>` 为 `1`。GPU 配置仍要求
  容器内 `device="cuda:0"`，但不再存在可与设备状态冲突的重复字段。
- 日志与热词：移除未使用的 `hotword_path`。日志不再接受 `log_path`，固定写入
  `asr_offline/logs/asr_service.log`，按本地时间每日轮转、保留 7 个归档；目录为运行时生成且被 Git 忽略。
- 同步范围：本地 ASR 和平台 GPU 部署 TOML、ASR README、平台的本机 CPU 复验命令、配置契约测试及变更记录都已更新。
- 验证证据：`asr` Python 3.11.13 运行 `compileall`、应用导入、`pip check`
  与完整 `unittest` 全部通过（57 项）；平台部署配置/Compose 聚焦测试 5 项通过。冷启动后
  `/get_status.appVersion="asr:latest"`、`logs/asr_service.log` 存在；对仓库外 442.853878 秒法语
  MP3 发起 `v1.1.8 language=fr` 真实推理，返回 134 段、1036 个词，时间戳合法且
  单调，1/5/10 分钟 `speed_info` 完整。

## 2026-08-14 - FaceRec PyPI 下载超时与可续接构建

- 失败证据：提交 `8d5e63718bba56225fd0eda0f05935a6a4c9c84c` 的真实八镜像构建已完成
  ASR Offline、ASR Online、OCR 和 VBas，且四个 revision 均精确匹配。FaceRec 在
  `pip3 install --upgrade pip setuptools wheel` 中从 `files.pythonhosted.org` 下载
  1.8 MB 的 pip Wheel，到 0.3 MB 时触发 `ReadTimeoutError`；串行构建因此停止，
  ScreenDet、PPT Slice 和 Text Analysis 未开始。
- 根因：FaceRec Dockerfile 的构建工具升级和 `requirements.txt` 安装都没有使用可配置
  镜像源、显式超时、重试和 Wheel 优先；默认 pip 读超时过短，与已批准的
  “可达且有进展就等待”构建策略不一致。最终根盘仍有约 145 GiB，内存约
  121 GiB 可用，已排除磁盘门禁和 OOM。
- 修复边界：只为 FaceRec 新增可覆盖的 `PYPI_INDEX_URL`，默认使用已验证可达的
  清华镜像；两条网络 pip 安装均设置 `--timeout 300 --retries 10 --prefer-binary`。
  由于 `fastdeploy-gpu-python==1.0.7` 不在清华或官方 PyPI，业务依赖安装还通过
  可覆盖的 `FASTDEPLOY_FIND_LINKS` 访问 Paddle 官方 Wheel 页；真机已确认该页面包含
  `fastdeploy_gpu_python-1.0.7-cp310-cp310-manylinux1_x86_64.whl`。
  不改动 FaceRec HTTP 契约、Python/FastDeploy 版本、模型、MongoDB 或 GPU 运行方式。
- 续接边界：必须在包含本修复的新完整 SHA 上重新调用统一 `build-images`。
  前四镜像和 FaceRec 已完成的 apt 层可命中 BuildKit 缓存，但仍要以八个最终
  inspect 和新 revision 为准，不得沿用旧 SHA 宣称整体通过。

## 2026-08-14 - OCR 模型清单权威投影与构建续接

- 失败证据：目标服务器在提交
  `824acbcf8f87c10739abffb936fc750b6f0fe92b` 上成功构建 ASR Offline 和
  ASR Online；两个镜像的 revision 均与该提交一致。OCR 在 runtime 阶段执行
  模型校验时因 `/app/models/manifest.sha256` 不存在而失败；后续五镜像未开始。
- 根因：外部 `model-assets.manifest.json` 是六个模型根的唯一交付权威，且资产事务会
  原子替换整个 `ocr/models`；OCR Dockerfile 和运行时引擎却保留了从同一目录读取
  旧 `manifest.sha256` 的假设，形成跨组件契约冲突。失败不是 Wheel 下载、Docker
  `COPY`、磁盘或 GPU 问题。
- 修复边界：`build-images` 从 Git 工作树外的总清单投影 OCR 子集到临时 `0600`
  文件，仅通过必需的 BuildKit secret 交给 OCR。镜像先校验精确文件集与全部摘要，
  再安装引擎需要的派生运行时清单；临时文件在成功、失败或中断后删除。
  不向 Git 或模型源增加第二个权威清单，也不在镜像内重新计算哈希自证。
  资产生成、发布和校验现在都机制化拒绝六个外部模型根中的任何 `manifest.sha256`。
- 下载策略：Wheel 可在 Docker build 内在线下载。只要网络字节、Docker 缓存或磁盘写入
  仍有进展，即使日志暂时静默也继续等待；只对明确 403/404、连接失败或持续无字节
  进展停止。持续无进展默认按 60 秒采样、15 分钟观察窗口判定，任一证据增长即重置窗口。
  构建阶段由多个独立监控任务跟踪进度、资源和镜像矩阵。
- 验证边界：已以先失败后通过的回归覆盖外部清单投影、工作树边界、未声明文件/
  目录符号链接拒绝、Docker secret 契约和临时文件清理。八镜像仍需在包含本修复的
  新完整 SHA 上重新执行，在八个 inspect 全部通过前不宣称构建完成。

## 2026-08-14 - OCR 可选 Cython 构建与双项目同步

- 先前状态：源项目和算法功能调度 OCR 副本的 CPU/NVIDIA GPU 镜像均直接交付完整 Python
  业务源码；目标副本另有 operator registry、GPU 门禁、registry wheel 和平台 entrypoint，不能整目录覆盖。
- 目标状态：同一份 `docker/Dockerfile` 默认构建源码镜像，仅在
  `--build-arg cython=yes` 时编译 16 个功能模块；最终镜像不保留核心源码、编译中间产物、
  编译器、Cython、依赖清单或正式配置。目标项目继续使用平台 wheel、`app.main:app` 和 entrypoint。
- 变更文件：目标 OCR 的 `.dockerignore`、`app/main.py`、`docker/Dockerfile`、
  `docker/build_cython.py`、`docker/README.md`、Docker/Cython/入口测试，以及中央 Harness 的
  决策矩阵、验证入口和 `harness/scenarios/ocr-optional-cython-build-and-sync.md`。真机验收后还修正
  `LD_LIBRARY_PATH`，防止 CUDA compat 驱动覆盖宿主机 NVIDIA 驱动，并增加回归测试。
- 契约影响：`/ocr/getVersion`、`/ocr/prediction`、响应字段、端口 8866、模型和
  `device = "cuda:<index>"` 格式不变；正式配置仍只允许宿主机挂载。两个项目的 NPU Dockerfile 未修改。
- 同步版本：源项目最终修复提交为 `main@797968c9eca8e51f5d52d62b94c38e8c517e30ed`；
  目标 OCR 最终修复提交为
  `codex/milestone-2b-three-gpu-deployment@a5106d026b1aa58ed33f9125a0cb67b53e5e25c4`。
  两边模型摘要一致，模型目录未读取、复制或删除；目标已有无关未跟踪文档保持原状。
- 验证命令与环境：MacBook / Docker Desktop 使用 `linux/amd64`；源项目 `161 passed`，
  目标项目 `165 passed`；四个修正版镜像完整构建成功。真实 x86_64 主机
  `192.168.29.11` 使用同一只读 GPU 配置、`--gpus all`、`REQUIRE_GPU=true` 和物理 GPU 2，
  完成四个最终镜像的版本接口、真实 OCR、公式开启、显存及重启验证。
- 真机根因与结果：初次运行因 CUDA 11.8 compat 的 `libcuda.so.520.61.05` 抢先加载而返回
  CUDA 错误 803；修复后四组 OCR/公式响应摘要完全一致，进程显存为 `2414-2424 MiB`，
  重启前后 OCR 逐字一致，结束后无验收容器且三张 GPU 均恢复到 `3 MiB`。
- 证据等级与结论：达到静态/单元/契约、本机 CPU 真实推理、Docker 完整构建、真实 NVIDIA
  GPU 推理和容器重启层级；目标平台专属入口、wheel、GPU 门禁和 entrypoint 已保留。
  综合结论为符合。
- 剩余风险：Cython 是编译保护，不是密码学加密；后续更换 CUDA 基础镜像或驱动版本时仍需
  复验宿主机 `libcuda` 的加载优先级。

## 2026-08-12 - 里程碑 2B 远端部署执行记录（模型资产与镜像前置）

- 执行目标：在 `192.168.29.11` 上按里程碑 2B 计划推进三卡部署；本记录只收纳本次
  真实远端执行事实，不把本地 Harness 结果当作服务器通过。
- 已通过：服务器预检、x86_64/三卡/Docker/Compose 检查、代码提交
  `855109bf5e746f97a6caf4856b733eb9127c405e` 固定、运行容器快照、课程/结果目录准备、
  外部模型资产传输、模型 staging 和六个模型根的逐文件字节/哈希校验。
- 模型资产：受控源位于 Git 工作树外；清单生成前仅移除 staging 源中的两个明确污染文件
  `vbas/models/.DS_Store` 和 `ocr/models/manifest.sha256`，未触碰任何原始算子模型目录。
  服务器校验结果为：ASR Offline 76 个文件、ASR Online 10 个文件、OCR 13 个文件、VBas
  8 个文件、FaceRec 3 个文件、ScreenDet 4 个文件，全部 PASS。
- 未通过/未执行：八镜像尚未构建，平台/基础设施/24 个算子实例尚未启动，GPU 真实性、注册、
  Smoke、反例、压测、恢复和完整泳道均未执行。
- 阻塞根因：第一个镜像依赖的
  `nvcr.io/nvidia/cuda:12.1.1-cudnn8-runtime-centos7` 在服务器下载最后一个大层时发生
  TLS/registry 长时间重试；两次有界预拉取均未形成完整本地镜像。`nvcr.io/v2/` API 可达，
  因此问题定位为大层传输/registry 稳定性，不是 Git、Dockerfile、模型清单或磁盘门禁失败。
- 操作边界：构建脚本在第一个镜像失败后按设计短路；未使用替代基础镜像、未执行
  `docker system prune`、未删除现有容器/数据卷、未启动不完整部署。
- 续接条件：恢复稳定的 `nvcr.io` 大层下载（或由用户提供同一 digest 的内部镜像缓存）后，
  从“预拉取基础镜像/八镜像构建”继续；构建成功后严格按
  `harness/scenarios/milestone-2b-deploy.md` 的阶段顺序执行。
- 证据位置：服务器上的运行日志按 release/SHA 归档；模型逐文件 manifest 保留在 Git 外受限
  目录，不写入仓库或报告。密码、私钥和模型原图未写入 Git、文档或命令参数。

## 2026-08-14 - 里程碑 2B 八镜像构建续接与 ASR Offline 基础环境阻塞

- 续接前置：用户已在目标服务器成功拉取
  `nvcr.io/nvidia/cuda:12.1.1-cudnn8-runtime-centos7`；远端检查确认镜像为
  `linux/amd64`，代码仍固定在 `855109bf5e746f97a6caf4856b733eb9127c405e`，工作树干净，
  六个模型根再次逐文件校验 PASS，根分区剩余约 212 GiB。
- 实际执行：重新运行八镜像构建入口；注册客户端 wheel、构建上下文门禁、模型门禁和
  ASR Offline 的 CUDA 基础镜像阶段均通过。CentOS/Conda 系统依赖安装完成后，构建在
  `requirements-pip.txt` 的 `torch==2.7.0` 解析处终止；后续七个镜像未开始。
- 表面错误：Dockerfile 将 pip 主索引固定为阿里云镜像，该镜像当前只列出到
  `torch 2.6.0`，因此报告 `No matching distribution found for torch==2.7.0`。
- 根因：官方 PyPI 的 `torch 2.7.0` Python 3.11 x86_64 wheel 为
  `manylinux_2_28`，要求 glibc 至少 2.28；当前 CentOS 7 CUDA 基础镜像只有 glibc 2.17。
  仅增加官方 PyPI 索引只能解决“找不到版本”，不能解决运行时 ABI 不兼容。
- 决策边界：未降级 Torch/Torchaudio、未强行升级容器 glibc、未切换未经确认的 CUDA/cuDNN
  组合、未继续构建后续镜像。已确认同 CUDA/cuDNN 版本的
  `nvcr.io/nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04` tag 存在；下一步需由用户准备该
  基础镜像，再以测试覆盖的最小 Dockerfile 变更将 ASR Offline 系统包管理从 yum 改为 apt。
- 当前结论：八镜像构建仍为失败；基础设施、平台、24 个算子实例及所有真实运行验收保持
  “未执行及原因”，不得宣称里程碑 2B 部署完成。

## 2026-08-14 - ASR Python 3.11 + Torch 2.6 真机验证与八镜像再次续接

- 决策覆盖：上一条记录中“不降级 Torch”的边界已被后续用户决策覆盖；ASR 镜像现采用
  Python 3.11、Torch/Torchaudio 2.6.0，并继续使用已经准备好的
  `nvcr.io/nvidia/cuda:12.1.1-cudnn8-runtime-centos7`。未升级 CentOS 7 的 glibc。
- 真实构建：目标服务器成功构建 `seacraft-asr-offline:v1.0_260812`。容器内实测为
  Python 3.11.15、Torch 2.6.0+cu124、Torchaudio 2.6.0+cu124，
  `torch.cuda.is_available()` 为真，识别到 RTX 4090 D。
- 真实推理：通过 `/v1.1.8/seacraft_asr` 发起真实请求并取得 HTTP 200；返回文本非空、
  `segments=1`、`gpu_time_ms=899.94`。推理期间宿主 `nvidia-smi` 可见进程名
  `asr_offline`，显存约 4696 MiB。隔离验证容器随后停止并删除，镜像保留。
- 八镜像续接：ASR Offline 命中构建缓存；ASR Online 命中 CUDA 基础镜像缓存后，官方
  `repo.anaconda.com` 的 Miniconda 安装器下载约 200 秒仍未完成，按有界停顿规则主动中止，
  未继续后续六个镜像。
- 网络证据：同一服务器对 Miniconda 安装器地址测速，官方源约 0.60 MB/s，清华镜像约
  5.43 MB/s，中科大镜像返回 HTTP 403。根因定位为构建期间的安装器下载源吞吐，不是
  Python 3.11、Torch 2.6、CUDA、模型资产、构建上下文或磁盘门禁失败。
- 修复决策：ASR Offline/Online Dockerfile 将 `MINICONDA_BASE_URL` 和安装器文件名定义为
  build args，默认使用清华镜像，仍允许交付环境显式覆盖；对应合同测试固定默认源与参数化
  下载行为。
- 当前边界：ASR Offline 的单镜像构建和真实 GPU 推理已经通过；八镜像整体、24 个算子
  实例、平台注册、全部 Smoke/反例/压力/恢复和完整泳道仍未完成，不得外推为里程碑 2B
  已通过。

## 2026-08-14 - Miniconda 镜像 GET 行为复核与默认源纠正

- 失败复现：提交 `6b9376b3abf7d70f80ad71e337d96fe23d059fa8` 在目标服务器重新
  执行八镜像构建，模型/上下文/wheel 门禁均通过，但 ASR Offline 的 Miniconda 下载在约
  16 秒后以 wget exit 8 终止，构建脚本按设计短路。
- 根因证据：清华入口对 curl HEAD 返回 302，并最终指向南京大学镜像的 HTTP 200；但在
  ASR Offline 的同一 CentOS 7/wget 环境中执行真实 GET 时，清华入口直接返回 HTTP 403。
  先前测速只证明站点吞吐，没有验证 Docker 构建所用客户端的 GET 行为。
- 对照验证：同一容器对最终地址
  `https://mirror.nju.edu.cn/anaconda/miniconda/Miniconda3-py311_23.11.0-2-Linux-x86_64.sh`
  执行真实 GET，4 秒下载 141613749 字节，退出码为 0。
- 修正决策：清华镜像不再作为默认值；两个 ASR Dockerfile 的 `MINICONDA_BASE_URL` 默认
  改为南京大学镜像，build arg 覆盖能力保留。上一条“默认清华镜像”的决定被本条明确覆盖。
- 当前边界：本条只关闭 Miniconda 下载源兼容问题；新的完整 SHA 尚需再次执行八镜像构建，
  在八个镜像均通过 inspect 前不得声明构建完成。

## 2026-08-14 - OCR 示例配置与镜像输入门禁集成修复

- 失败复现：提交 `fa07ea30a2e4dc15dbe86bf785123ca458f6f7f0` 在正式 Docker build
  前通过模型校验，但输入门禁拒绝 OCR 的 `!config.toml.example`，八镜像构建按设计短路。
- 根因：OCR 可选 Cython 构建会使用无敏感值的示例配置执行构建期导入校验，校验后删除；
  原平台门禁把该精确示例配置与宿主正式 `config.toml` 视为同一类受保护重包含项。OCR 项目
  测试和平台镜像门禁因此出现跨提交契约不一致。
- 修复边界：只为 OCR 允许精确的 `!config.toml.example`；正式 `config.toml`、其他
  `!config*` 重包含和其他算子配置仍被拒绝。Dockerfile 合同测试改为只禁止精确复制正式
  配置，不再误拒绝 `COPY config.toml.example ...`。
- 验证证据：镜像构建测试 64 项全部通过；真实八上下文及 Git 输入门禁 PASS；新增正例证明
  OCR 示例配置可进入构建期，新增反例证明 `!config.toml` 仍被拒绝。
- 当前边界：门禁与 OCR 新构建合同已对齐，但八镜像真实构建仍需使用包含本修复的新完整 SHA
  重新执行，不能仅依据本地门禁结果声明镜像通过。

## 2026-08-14 - 八镜像续接至 Torch CUDA wheel 下载后有界中止

- 执行提交：目标服务器 detached checkout
  `c36dbc45c4c3a7e721785eb4a5cd8e12757c8cd4`，重新执行统一八镜像构建入口；模型资产、
  八上下文、Git 输入和 registry wheel 门禁全部通过。
- 已越过阶段：ASR Offline 从南京大学镜像下载并安装固定 Miniconda 安装器；Conda 环境
  成功创建，镜像内确认 Python 3.11.15、glibc 2.17。此前 Miniconda 和 OCR 门禁阻塞均未复现。
- 下载事实：`torch-2.6.0` 的 766.7 MB wheel 下载完成，用时 10 分 26 秒，平均约
  1.4 MB/s；随后解析到 PyTorch CUDA 12.4 拆分依赖，下载速率一度降至约
  0.2-0.6 MB/s。完整依赖集合还包含 `nvidia-cublas-cu12==12.4.5.8`、
  `nvidia-cudnn-cu12==9.1.0.70`、`nvidia-cufft-cu12==11.2.1.3`、
  `nvidia-curand-cu12==10.3.5.147`、`nvidia-cusolver-cu12==11.6.1.9`、
  `nvidia-cusparse-cu12==12.3.1.170`、`nvidia-cusparselt-cu12==0.6.2`、
  `nvidia-nccl-cu12==2.21.5`、`nvidia-cuda-{nvrtc,runtime,cupti}-cu12==12.4.127`、
  `nvidia-nvjitlink-cu12==12.4.127`、`nvidia-nvtx-cu12==12.4.127` 和
  `triton==3.2.0`。
- 中止边界：在 `nvidia-cuda-cupti-cu12==12.4.127` 下载期间主动 Ctrl-C；BuildKit 报
  `context canceled`，构建脚本停止在 ASR Offline，后续七个镜像未开始。没有清理 BuildKit
  缓存、已有镜像、容器或业务数据。
- 续接条件：通过更快镜像/内部代理准备上述 Python 3.11 x86_64 wheels，或接受当前带宽下的
  长时间单次构建窗口后，再从统一构建入口继续。ASR Python 3.11 + Torch 2.6 的真机 GPU
  推理结论仍有效，但八镜像整体保持未完成。

## 2026-08-12 - 里程碑 2B Task 10-11 文档与本地验收边界

- 先前状态：Task 7B-9 的构建上下文、模型资产事务、GPU 证据采集器、注册/Smoke
  和报告归档代码已经通过各自的本地行为门禁，但执行顺序、证据等级、服务器前置
  条件和真实部署未在一个场景中收敛；裸 `pytest` 可能被同名外部 `tests` 包遮蔽。
- 目标状态：新增 `harness/scenarios/milestone-2b-deploy.md`，固定从预检、容器
  快照/暂停、基础设施、模型 staging、八镜像构建、逐卡 Compose、GPU 证据、注册、
  八类 Smoke、反例/压力/恢复、恢复业务容器到报告渲染的顺序；所有命令使用当前
  CLI 和完整 Git SHA，并要求 `PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest`。
- 变更文件：Task 10-11 Harness 场景、验证命令、部署 README、单机运维手册和本台账。
- 契约影响：不改变 A 面、算子 HTTP/WebSocket、Compose 端口、实例拓扑或服务边界；
  只收紧证据归档和部署操作的可复现性。
- 服务器前置：目标为 `root@192.168.29.11:22`，代码目录为
  `/root/workspace/algorithm-scheduling`，架构 x86_64、三张 NVIDIA GPU、Docker
  NVIDIA runtime、`/data/course` 与 `/data/result` 可写，并具备 PostgreSQL、Redis、
  Kafka、MongoDB。登录凭据和私钥只通过外部安全通道提供，禁止进入仓库、报告和命令历史。
- 当前证据等级：Task 7B-9 为静态/脚本行为/本地文件系统层级；里程碑 2A 为真实
  PostgreSQL/Redis/Kafka、服务运行和契约 Stub 层级。尚未取得目标服务器三卡、真实模型、
  24 实例注册、真实媒体推理或完整离线/在线泳道证据。
- OpenSpec 状态：只保留已有真实证据对应的 2.3-2.6、4.1-4.6、4.13-4.14、8.1-8.5
  勾选；7.4、7.5、4.7-4.12、视觉、在线、完整产品和真实部署任务不提前勾选。
- 剩余风险：ScreenDet 仅属于 `online-gateway-service`；PPT 真实课程视频仍需终态
  manifest/回调证据；ASR/VBas fixture 和六根模型 manifest 需要在服务器外部受控提供。

## 2026-08-12 - 里程碑 2B GPU 实例证据采集器（Task 8）

- 先前状态：设计要求为 18 个 GPU 实例留存 CUDA/PID/容器归属证据，但尚无可执行采集器，只看环境变量或空闲模型进程会产生假通过。
- 目标状态：`verify-gpu-instance` 将容器声明、容器内 `nvidia-smi` 唯一卡/UUID、按算子选择的 Torch/Paddle/FastDeploy 框架探针、真实触发存活期间的宿主 CUDA PID、进程名、`docker top`、完整 cgroup ID 和 `NSpid` 组合为单一证据链；停止模式只跟踪先前精确映射的 PID。
- 变更文件：GPU 采集 CLI、fake 运行时行为测试、部署说明、Harness 场景与验证入口。
- 契约影响：算子 HTTP/WebSocket、端口、Compose 实例数和平台调度契约不变；只新增部署验收工具。
- 验证命令与环境：macOS CPU 上用 fake `docker`、`nvidia-smi` 和 `/proc` 执行聚焦测试、Ruff、严格 Mypy 和 `py_compile`；不连接远程服务器。
- 证据层级与结论：验收工具单元/脚本行为层级符合；不计为真实 GPU、真实推理或三卡部署通过。
- 问题收纳：停止模式首版仅比对历史 PID，会把被其他容器复用的宿主 PID 误判为残留；已通过当前 cgroup 完整 ID 复核修复。规格复审又发现默认 Torch 会误杀 OCR/FaceRec、样本提交前没有二次检查 trigger、历史证据 SHA 和 UUID DeviceRequests 解析不完整；均以先 RED 后 GREEN 的回归关闭。
- 质量复审：所有 Docker/NVIDIA/框架辅助命令增加统一可配但有界的超时；触发器在独立进程组中运行，任意失败都用 SIGTERM/SIGKILL 有界回收整组。真实父进程派生长驻子进程的测试已证明超时后两者均消失。
- 剩余风险：目标驱动对 compute-apps `gpu_uuid` 查询的支持仍需真机预检；MIG 不在本阶段范围。PID 映射尚未记录 `/proc/<pid>/stat` starttime，极端快速 PID 复用仍有 TOCTOU 风险，已作为后续加强项记录。尚未勾选任何需要真实 GPU 证据的 OpenSpec 任务。

## 2026-08-12 - 离线 ASR 五何能力退役与路由模块收敛

- 先前状态：`asr_offline` 仍暴露 `POST /text/question`，请求首次触发时会加载 BERT FiveWh 模型并驻留；v1.1.7 已退役，但保留的 v1.1.8 路由源码仍命名为 `asr_v18.py`。
- 目标状态：只保留离线 ASR、音频质量和运行状态能力；删除本算子的 FiveWh HTTP 路由、请求实体、特征整理、BERT 推理与配置，将唯一 ASR 路由模块收敛为 `app/api/routes/asr.py`。独立 `text_analysis` 算子不在本次变更范围。
- 变更文件：`asr_offline` 路由装配、模型/配置/实体/工具、Docker ignore、单元合同、README/AGENTS；平台共享 ASR 配置、部署源文件合同及本 Harness。历史设计和上一条多语言账本不回写。
- 契约影响：显式删除 `POST /text/question`，HTTP 实测为 404；`POST /v1.1.8/seacraft_asr` 的路径、请求、响应、处理函数名和 OpenAPI operationId 保持不变，`speed`、`speed_info` 与 `rate_factor=0.4` 不变。
- 资源边界：删除所有 BERT/FiveWh 直接加载点，并将本机约 `393 MB` 的 `model/bert-base-chinese/` 与约 `1.1 GB` 的 `model/bert_output/` 排除出 Docker context；两个 Git 忽略的本地目录不物理删除。Paraformer、VAD、标点、CAM++、emotion2vec 和 Whisper 均保留。`transformers` 仍由 FunASR/ModelScope 音频依赖链使用，不因删除 FiveWh 而贸然移除。
- 验证命令与环境：`asr` Python 3.11.13 / macOS CPU 环境执行 `compileall`、`app.main:app` 导入、完整 `unittest`、`pip check`、平台 ASR 配置/GPU fail-fast/部署/适配器/Harness 聚焦合同，并用临时 CPU 配置冷启动真实服务。
- 真实证据：算子完整测试 `53/53`、平台聚焦合同测试 `22/22` 通过；`/ops/health` 为 HTTP 200，OpenAPI 只包含 v1.1.8 而不含三个退役路由，三个退役路由实际均为 HTTP 404。v1.1.8 对 12 秒真实中文音频返回 6 个 segment、71 字符非空文本、原有 6 个顶层字段和 1/5/10 分钟 `speed_info`，operationId 仍为 `api_asr_v18_v1_1_8_seacraft_asr_post`。
- 证据层级与结论：达到算子静态/单元合同、本机冷启动和真实 CPU HTTP 推理层级；FiveWh 退役及 v1.1.8 内部模块重命名符合。本轮未重复耗时约 9 分钟的法语推理，沿用上一条中对未改动 Whisper 响应链路的真实样本证据；未达到 GPU 容器、真实租约、Kafka 或课程 DAG 验收层级。
- 剩余风险：仓库外旧报告流水线 `/Users/zhangshen/Documents/workspace/ai报告分析课程数据/scripts/pipeline.py` 仍调用 `/audio/detect_mandarin` 和 ASR Offline `/text/question`。后者失败虽会被捕获，但报告会缺失五何结果；发布前必须迁移或确认停用。`text_analysis` 的同路径实现可作为候选，但依赖外部 LLM 且响应语义并非逐字段等价，不能只替换端口而不做报告回归。

## 2026-08-12 - 离线 ASR v1.1.8 多语言收敛与资源缩减

- 先前状态：离线 ASR 同时暴露 `v1.1.7`、`v1.1.8` 和普通话检测路由；小语种路径保留了当前调度不需要且额外占用模型资源的 Pyannote 说话人链路，v1.1.8 未承接法语 Whisper 响应合同。
- 目标状态：唯一离线转写接口收敛为 `POST /v1.1.8/seacraft_asr`；`auto/zh/en` 使用 Paraformer，只有白名单小语种 `fr` 使用 Faster-Whisper。`open_mul_lang=false` 或模型未就绪时返回 HTTP 200 / `4003`，空语言或未支持语言返回 HTTP 200 / `4009`。
- 变更文件：`asr_offline` 路由、请求实体、Whisper 并发/响应组装、模型与功能配置、requirements、Docker/Compose、合同测试和说明文档；同步更新本 Harness 账本、验证命令和算子场景。
- 契约影响：删除 `POST /v1.1.7/seacraft_asr` 和 `POST /audio/detect_mandarin`；保留 `/audio/db_snr` 和 `/text/question`。成功响应不新增顶层字段；法语请求的 `role`/`emotion` 按需返回 `null`，`segment_words` 在关闭词时间时为空数组、开启时为真实 Whisper 词时间。`rate_factor=0.4` 保留且只作用于单段 `speed`。
- 资源边界：移除 Pyannote 代码、直接依赖、配置、Docker 改写和 Compose 兼容环境变量；三个退役 Pyannote 模型目录只从镜像上下文排除，不删除本地文件。Paraformer、CAM++、emotion2vec、Whisper 和 FiveWh 均保留，FiveWh 按请求懒加载。
- 验证命令与环境：`asr` Python 3.11.13 / macOS CPU 环境执行 `compileall`、完整 `unittest`、`pip check`、平台 ASR 配置/部署/适配器聚焦合同测试、冷启动/OpenAPI/HTTP 路由验证，并对 `/Volumes/Data55/asr测试文件/法语音频.mp3` 执行真实 Faster-Whisper 推理。
- 真实证据：算子完整测试 `50/50`、平台聚焦合同测试 `20/20` 通过，两个退役路由均为 HTTP 404。`442.853878` 秒法语样本在 CPU 上约 `536.8` 秒完成，返回 140 个 segment、1063 个词时间、139 个正数 `speed` 和 8/2/1 个 1/5/10 分钟 `speed_info` 窗口；请求的 `role`/`emotion` 全为 `null`，顶层字段严格保持既有 6 项。
- 证据层级与结论：达到算子静态/单元合同、本机服务运行和真实 CPU 推理层级，未达到通过 `control-service` 真实租约调用的算子契约验证，不将其计为 Kafka、课程 DAG、GPU 容器或三卡部署完成证据。
- 剩余风险：仓库外旧报告流水线仍调用已退役的 `/audio/detect_mandarin`；发布前必须迁移该步骤或确认整条流水线已停用。

## 2026-08-12 - 里程碑 2B 模型资产与密钥边界（Task 7C）

- 先前状态：设计错误地列出七个模型目录并包含 VBas 加密目录；ScreenDet 运行读取 `model/screen.pt`、`model/occlusion.pt`，但 Dockerfile 没有复制模型且 `.dockerignore` 排除了模型；多个镜像仍可能复制本地配置或整个项目上下文。
- 目标状态：只交付六个实际明文模型根；仓库外源目录用精确 manifest 冻结全部普通文件，经过全量预校验后以锁、持久 journal、fsync、同文件系统 stage/backup 和原子重命名发布；八镜像统一使用 Compose 只读配置挂载。
- 变更文件：`deploy/model-assets.json`、模型生成/发布/验证与 runtime secret 脚本、`build-images`、八个 Dockerfile/`.dockerignore` 的必要边界、2B 设计/部署说明及行为测试。
- 契约影响：HTTP/WebSocket、算子端口、模型路径和 Compose 实例数不变。ScreenDet 明文模型现在明确进入镜像；VBas 当前镜像只含 `models`，不含 `models-encrypted` 或密钥。
- 故障证据：测试覆盖源在 worktree、符号链接、FIFO、缺失/额外、字节/hash 篡改、密钥/加密路径、缓存污染、复制阶段失败，以及 backup 后、replace 后、journal fsync 后中断恢复；目标不存在和已有旧目标两种切换均可重入。
- 构建输入补强：Git 输入门禁将矩阵中显式 `-f` Dockerfile 视为发布输入，即使 `.dockerignore` 排除该路径，未提交修改或删除仍会阻断构建。
- 配置与密钥：八个本地 `config*.toml` 不进入 context，服务器配置只由 Compose 只读挂载；runtime secret 校验只检查 ID、目标、普通文件、owner 和精确 `0600`，不读取内容、不记录 size/hash。当前明文模式不要求 secret。
- 已知风险：ASR Online 的 `.enc` 模型仍使用源码硬编码解密材料，本任务未扩大为业务模型加密改造，不能将其描述为安全密钥保护。未来 VBas/ScreenDet 加密模式必须使用独立只读 secret mount，且加密镜像不得同时内置明文权重。
- 完成边界：本任务提供资产和镜像输入门禁；尚未在目标 x86/NVIDIA 服务器完成八镜像真实构建、24 实例启动或推理，不勾选 OpenSpec 7.4。

## 2026-08-12 - 里程碑 2B 八镜像构建输入冻结（Task 7B）

- 先前状态：Compose 已引用八个版本化镜像，但没有单一的 context/Dockerfile/image 矩阵、统一构建入口或 Docker build context 机器门禁。
- 目标状态：用 `deploy/operator-images.tsv` 冻结八镜像矩阵；`build-images` 从任意目录分发 registry wheel，并按固定顺序构建、检查磁盘、附加 Git SHA label 和 inspect 终态校验；上下文门禁拒绝矩阵漂移、工作区根 context、越界 `COPY/ADD` 和常见污染输入。
- 变更文件：八镜像矩阵、`build-images`、`verify-operator-build-contexts`、八个 `.dockerignore`、行为测试和部署说明。
- 契约影响：算子 HTTP/WebSocket 契约、端口、Compose 实例数和模型目录名不变；只收紧镜像构建输入与发布标签。
- 验证命令与环境：Pytest 使用 fake Docker/df/Git 和临时工作区验证八镜像顺序、任意 cwd、失败短路、镜像引用/label inspect、磁盘门禁和上下文污染拒绝；真实工作区八个 context 门禁通过；本任务未构建大型镜像。
- 证据等级与结论：镜像构建管道单元/脚本行为及真实静态 context 门禁符合；尚未达到真实 Docker 镜像构建或容器运行证据。
- 已关闭风险：FaceRec 不再把 `media/`、本地 `config.toml`、Harness/OpenSpec/Codex 状态纳入 context；PPT 排除本地 Harness 大数据；ASR Offline 的模型 hotword WAV 用 `!model/**/*.wav` 从全局媒体排除规则中恢复。
- 规格复审补强：门禁只允许按算子精确声明的 negation，拒绝 `!*`、`!**`、`!**/*` 及其他宽泛重包含；拒绝 HTTP/HTTPS/Git 远程 `ADD`；遍历真实 context 文件并按 `.dockerignore` 顺序与 negation 计算最终 inclusion，阻止未忽略的媒体、测试、缓存与密钥制品。扫描实际发现并修复 VBas 模型 allowlist 重新纳入 `__pycache__` 和 ScreenDet 漏排 `tests/` 的问题。
- 质量复审补强：`build-images` 必须接收与 HEAD 精确一致的 40 位 `EXPECTED_GIT_SHA`，并在构建前拒绝会进入镜像 context 或 registry wheel 的 tracked dirty/untracked 源，但允许被 `.dockerignore` 排除的用户文档/测试变更和受 7C 管理的模型资产。Dockerfile 有限解析器支持 TAB、escape directive、续行、JSON 及 `--from` 两种形式，并对未知/解析失败 fail closed。镜像 inspect 校验 `RepoTags` 列表包含目标引用，不再依赖列表第一项。
- 质量二次复审：Git 输入门禁同时检查 HEAD 中原本会进入 context 但已被删除的 tracked 文件；删除已排除的测试文件仍允许。八个 `.dockerignore` 统一排除 `wheel/*.whl`，仅精确重包含 `algorithm_operator_registry_client-0.1.0-py3-none-any.whl`，ASR Offline 现有 PyArrow wheel 不再进入构建 context。
- 交付准备结论：Task 7C 已通过六个实际明文模型根的仓库外 manifest、事务暂存/校验与密钥边界门禁关闭交付准备；真实服务器资产传输和镜像构建仍待后续任务。

## 2026-08-11 - 方案 C 里程碑 2A 真实运行时闭环

- 先前状态：Kafka adapter、Outbox Publisher、Consumer、DAG、租约执行器和契约 Stub 只有组件或 Broker 级测试，没有真实服务进程贯通证据。
- 目标状态：用真实 PostgreSQL、Redis、Kafka、`control_service.app.main:app`、`orchestrator_service.app.main:app` 和独立 HTTP Stub 验证 ASR-only 调度、恢复和幂等。
- 变更文件：里程碑 2A 运行时集成测试、一键运行脚本、可延迟契约 Stub、orchestrator readiness 故障注入测试、gitignore 运行报告目录、Harness 证据文档和 OpenSpec 任务状态。
- 契约影响：A 面字段、任务类型、HTTP 路径、算子 `/execute` 请求/响应和默认端口均不变；延迟只由测试 Stub 环境变量控制。
- Kafka 客户端决策：平台选用 `aiokafka` 0.14.x，以原生 asyncio API 实现确认发送、手动提交、有界轮询和 lag；实装 0.14.0 元数据为 `Requires-Python >=3.10`，与平台 `requires-python>=3.11` 兼容，orchestrator 显式限定 `aiokafka>=0.14,<0.15`。该依赖属于平台，不进入算子模型环境。
- 验证环境：`postgres:17-alpine` 17.10、`redis:7.4-alpine` 7.4.10、`apache/kafka:4.0.0` 均为 healthy；每次运行使用唯一 `_test` 数据库、Redis DB 14 UUID 前缀、唯一 Topic/Group 和临时端口。
- 真实证据：NORMAL/URGENT 请求先到状态 30，再经首次心跳恢复到节点/任务 60；GET 观察到运行中 50；Kafka offset 从 2 恢复到 4；重复发布后 Outbox 尝试次数为 2，仍只有 2 个任务类型和 4 个节点；URGENT Stub 调用先于 NORMAL；终态租约为零。
- 实例选择证据：E2E 在节点执行轮询期间从本次唯一 Redis 前缀的 `lease:*` hash 采集 `lease_id`、`instance_id`、`capability`、`service_url` 到 `evidence.selected_instances`，而非从注册响应推断。断言实际观察到 `asr_offline` 与 `text_analysis`、实例 ID 是本次对应注册实例、URL 均为 Stub；采集后仍验证终态租约清零。
- 发布恢复证据边界：`tests/test_outbox_publisher.py` 通过组件故障注入验证发布失败时事件保持待发布；真实 Broker Harness 恢复待发布 Outbox 并重启 orchestrator，证明重投后 `published_at` 恢复、`publish_attempts>=2`。未停止真实 Broker，不将该证据表述为 Broker 停机演练。
- Kafka 不可用就绪证据：新增 `FakeConsumer.lag()` 故障注入用例，验证 `/ops/readiness` 返回 503、Kafka 检查为 `ready=false` 且中文诊断可见；不停止真实 Kafka 容器。该服务用例与真实 Broker 的发布/消费、手动提交、同 group 重启 offset、未提交重投和重复消息证据合并支撑 2.6。
- 规范复审：Stub 增加真实 `/health`，所有启动/readiness 只接受 HTTP 200；两次 orchestrator run 保存不同 PID、序号、探针响应、停止日志和退出码。teardown 只接受完整 `algorithm-test-milestone2a-<32 hex>` 名称，精确删除本次 Consumer Group 并验证消失后再删唯一 Topic。
- 历史清理：2026-08-11 复核时 Broker 实际存在 2 个而非先前报告的 3 个里程碑测试 group：`algorithm-test-milestone2a-c603501f7c894294a801bc6ec6c0237f`、`algorithm-test-milestone2a-cab7c092931149679a3796c687d3571b`。两者均按完整名称删除成功，随后 Consumer Group 列表为空；未删除其他 group。
- 证据等级与结论：达到消息代理集成、服务运行、算子 HTTP 契约和确定性重启恢复层级，里程碑 2A 符合。JSON 运行报告位于 gitignore 的 `harness/reports/milestone-2a/`。
- 状态同步复审：`compileall`、Ruff、严格 Mypy 均通过；平台 `276 passed`，orchestrator `17 passed`；两份真实集成文件与一键 Harness 各 `12 passed`且无 skipped；基础设施/平台 Compose 解析和严格 OpenSpec 校验通过。
- OpenSpec 状态：有证据的 2.3-2.6、4.1-4.6、4.13-4.14 和 8.1-8.5 标记完成；4.7-4.12、视觉、在线与真实算子任务保持未完成。
- 剩余风险：2A 只调用契约 Stub，没有接真实 PPT、OCR、离线 ASR 或 VBas；2B 的真实同步算子、异步 PPT 长租约和视觉编排仍需独立验收。ScreenDet 只属于在线网关，不属于离线 DAG。

## 2026-08-11 - 算子本机运行、注册 wheel 与 PPT 终态合同复核

- 先前状态：算子注册客户端依赖平台源码导入；FaceRec 无人物图片留存开关；ASR 环境名和 Python 版本不统一；PPT 平台回调拒绝真实 `dynamic_segments`，且没有失败终态路径。
- 目标状态：发布 Python 3.10+ 轻量注册 wheel；ASR 使用 `asr` Python 3.11；FaceRec 使用 `facerecapi` 并默认不保存人物图片；PPT 平台适配器完整接收最新终态合同。
- 变更文件：注册客户端包/构建测试、ASR/FaceRec 镜像与运行代码、PPT 平台适配器与测试、Compose、总体设计和 Harness。
- 契约影响：现有业务推理路径和字段不变；FaceRec 新增默认 false 的 `save_person_photo` 配置；PPT 内部终态增加 `dynamic_segments` 的平台接收与持久化。
- 证据：独立 wheel 构建与隔离导入、ASR/FaceRec/OCR/ScreenDet/VBas/Text Analysis/PPT 本机真实调用、PPT 回调和路径安全组件测试。
- 证据等级与结论：算子真实运行和 PPT 组件合同符合；课程 DAG、PPT 常驻运行时和真实课程 P 视频仍未验收。
- 剩余风险：FaceRec FastDeploy 阻塞 Python 3.11；online gateway 人脸管理路由尚未实现；ScreenDet/Text Analysis 通用就绪状态仍需增强。

## 2026-08-10 - PPT 视频输入字段规范化

- Previous state: PPT submission used the ambiguous `uri` field even though orchestrator supplied an already prepared absolute local file path.
- Target state: `video_path` is the canonical field, accepts remote URLs or absolute local paths, rejects relative paths, and keeps `uri` only as an operator-side compatibility input.
- Changed files: PPT request schema/API/tests/docs, orchestrator PPT adapter, platform contract tests, AGENTS and Harness scenario.
- Contract impact: orchestrator now emits `video_path`; the operator still accepts legacy `uri`, so staggered deployment remains compatible.
- Evidence: PPT unit/contract suite, real temporary local MP4 decode, platform adapter tests and operator HTTP smoke verification.
- Remaining risk: background orchestrator end-to-end execution remains outside this component contract change.

## 2026-08-06 - Runtime closure baseline

- Previous state: control and online have functional routes; orchestrator and vision entrypoints are health-only; Kafka adapters and real end-to-end evidence are absent.
- Target: four independently deployable FastAPI projects with annotated configuration, real lifespan resources and reproducible evidence.
- Contract impact: A and non-PPT operator contracts unchanged. PPT internal callback changes from Base64 per slide to shared files, atomic manifest and one terminal callback.
- Current evidence: component and PostgreSQL/Redis tests only. Broker-backed and complete service-runtime evidence remains pending.
- Remaining risk: long-running Worker loops, restart recovery, real operator images and full Compose have not yet been verified.

## 2026-08-06 - FastAPI delivery and PPT shared-result components

- Previous state: four service folders had uneven entrypoint/configuration layouts; no platform Compose existed; the platform PPT adapter still expected per-image Base64 callbacks.
- Target state: complete per-service FastAPI packages and annotated settings, a validated four-service single-machine Compose, and one platform-only PPT shared-path protocol.
- Changed files: `services/*/app`, four service `config.toml`/requirements/Dockerfiles, `deploy/docker-compose.platform.yml`, `services/orchestrator_service/ppt_slice.py`, `ppt_slice/app`, and related tests/docs.
- Contract impact: breaking internal PPT contract. Only snake_case submission, atomic `/data/result/{task_id}/ppt/manifest.json`, and one terminal metadata callback are accepted. A-facing and other operator contracts are unchanged.
- Verification: four-service structure/contract tests `33 passed`; PPT platform tests `9 passed`; PPT Conda tests `13 tests OK`; Compose config validation passed.
- Evidence tier and verdict: static/service component/operator smoke evidence is present. Broker-backed end-to-end evidence is not present.
- Remaining risks: orchestrator has not wired PPT submission/callback/reconciliation/lease components into its required runtime loop; vision and general DAG loops remain incomplete; platform images have not been built together in the final stack.

## 2026-08-07 - Root-level platform service relocation

- Previous state: four deployable services lived under `algorithm-scheduling-platform/services`, used `services.<service_name>` compatibility imports, and Docker builds copied the shared service tree.
- Target state: `control_service`, `orchestrator_service`, `vision_orchestrator_service` and `online_gateway_service` are independent workspace-root FastAPI projects; `algorithm-scheduling-platform` retains only shared packages, migrations, deployment definitions, cross-service tests and Harness.
- Changed files: four root service projects, platform packaging/tests/Compose/Makefile, root and platform AGENTS rules, design documents, Harness and the active relocation OpenSpec artifacts.
- Contract impact: HTTP/WebSocket paths, methods, fields, container ports, Kafka semantics and operator registration are unchanged. Only internal source paths, Python imports and Docker build contexts changed.
- Verification: four service suites `4/5/8/9 passed`; platform suite `192 passed`; Ruff and strict Mypy passed; three Compose files parsed; four images built with a root allowlist `.dockerignore` and returned `/health` HTTP 200; image inspection found no sibling service source; the expanded runtime/build/documentation old-path gate and strict OpenSpec validation passed.
- Evidence tier and verdict: static, unit, Compose, independent-image and service-runtime smoke evidence is complete for relocation. Broker-backed business end-to-end evidence remains outside this structural change.
- Remaining risks: the shared distribution still lives under `algorithm-scheduling-platform`; Orchestrator's FFmpeg image is large and slow to build; runtime closure work remains governed by the separate active change.

## 2026-08-07 - 方案 C 基础调度闭环与数据库说明基线

- 先前状态：开发顺序把真实 PPT 作为首条最小离线链路，但 PPT 正在独立优化；总体图没有清楚表达 control 只写 Outbox、orchestrator Publisher 从 PostgreSQL 读取后发布 Kafka 的方向；数据库迁移没有表和字段注释。
- 目标状态：一个基础阶段包含两个连续里程碑，先完成 `control-service` 的任务事实闭环，再完成 `orchestrator-service` 的通用运行时；使用契约 Stub 验证真实 PostgreSQL/Redis/Kafka，不依赖真实 PPT。10 张正式调度表及其全部字段具有中文说明。
- 变更文件：总体设计 V2、活动 OpenSpec、Harness 基础闭环场景、数据库逻辑模型、`0004_schema_comments.sql` 和迁移约束测试。
- 契约影响：A 面字段、HTTP/WebSocket 路径、算子协议和状态值不变；只调整实施顺序、完成口径和数据库元数据。
- 数据库审计：本机 `algorithm` 业务库当前无用户表；`algorithm_migration_test` 有 9 张调度测试表；`algorithm_repository_test` 有全部 10 张调度测试表；未删除、改名或修改任何现有表和数据。
- 当前证据：数据库注释迁移约束测试和迁移文件名检查已通过；在本轮新建并随后删除的临时验证库中顺序执行 `0001-0004`，得到 10 张表、92 个字段，缺失表注释和字段注释均为 0；基础 Broker 闭环尚未实现和验收。
- 证据等级与结论：DDL 静态契约符合；方案 C 的服务运行时仍为部分符合。
- 剩余风险：目标业务库尚未执行 `0001-0004`；Kafka adapter、Publisher、Consumer、Dispatcher 和契约 Stub 闭环待实现；PPT 最终内部契约仍由独立会话收口。

## 2026-08-07 - 方案 C 里程碑 1：control 事实闭环

- 先前状态：`control-service` 在应用构造期创建 Engine/Redis，真实入口未组合 PostgreSQL 算子审计，readiness 不区分存活与依赖就绪，Redis 注册、心跳和注销存在先读后写竞态。
- 目标状态：FastAPI lifespan 统一持有 PostgreSQL/Redis；课程事实与 Outbox 同事务；PostgreSQL 保存算子声明和运维事件；Redis 保存 TTL、实时生命周期和原子容量租约。
- 变更文件：`control_service/app/infrastructure/runtime.py`、`audited_operator_registry.py`、共享 Repository/Redis registry、`0005_operator_audit_and_status_comments.sql`、Control Compose/README 以及里程碑 1 测试。
- 契约影响：A 面字段、HTTP 路径、算子推理协议和状态值不变；A 面任务库故障明确为 HTTP 200 + 业务码 `50000`，注册/租约基础设施故障为 HTTP 503。新增运维历史查询 `GET /ops/operator-instances/{instance_id}/events`。注册激活规则调整为“`register` 返回 OFFLINE，首次成功心跳后 ONLINE”。
- 真实环境：`postgres:17-alpine`（PostgreSQL 17.10）与 `redis:7.4-alpine`（Redis 7.4.10）容器均为 healthy；集成测试每次创建唯一 `_test` PostgreSQL 数据库和 UUID Redis 前缀，结束后精确清理。
- 验证：里程碑 1 联合集成测试 `63 passed`；平台与 Control 完整回归 `255 passed`；其他三个服务回归分别 `5/8/9 passed`；Ruff、Mypy、compileall、迁移命名、Compose 解析和严格 OpenSpec 校验均通过，无 skipped。新增用例覆盖缺字段、未执行 `0005`、依赖故障响应、readiness 并行/总截止预算、DSN 原有 PostgreSQL options 保留、首次心跳与短暂心跳故障恢复。
- 证据等级与结论：里程碑 1 达到真实 PostgreSQL/Redis 集成和 FastAPI 运行时证据，结论为符合。方案 C 整体仍为部分符合，不得宣称完整调度闭环已完成。
- 剩余风险：里程碑 2 的 Kafka adapter、Outbox Publisher、Consumer、DAG 和契约 Stub 尚未实现；本机 `algorithm` 业务库未自动执行迁移；当前协议没有进程世代标识，同一 `instance_id` 只允许一个存活进程。同 ID 重注册会清理旧心跳和租约，未来若要支持新旧世代重叠，需单独设计世代令牌。

## 2026-08-10 - 架构图留存与里程碑证据边界

- 先前状态：总体设计 V5.4 用新的离线/在线/运维服务边界图替换了一体化组件全景图，无法在同一文档追溯此前讨论；“里程碑 1 闭环”也容易被误解为已经调用真实算子。
- 目标状态：历史总体组件图、当前服务边界图和方案 C 时序图同时保留并具有稳定编号；后续架构图只追加不覆盖。明确里程碑 1 只达到真实 FastAPI/PostgreSQL/Redis 的 control 事实闭环，里程碑 2A 使用真实 Kafka 和 HTTP 契约 Stub，2B 再接首个真实同步算子。
- 变更文件：`docs/算法功能调度平台总体设计-v2.md/.pdf`、Harness 变更记录与架构证据矩阵。
- 契约影响：A 面和现有算子业务接口不变；进一步明确平台任务状态由编排服务推进，算子不得直接写平台任务状态。
- 验证：Markdown Mermaid 代码块结构检查、PDF 渲染与逐页视觉检查。
- 证据等级与结论：文档与架构决策记录符合；里程碑 2A/2B 仍未实现。
- 剩余风险：真实 Kafka、运行中的 DAG、契约 Stub 调用及真实算子接入均待里程碑 2 验证。

## Record template

- Date and scope:
- Previous state:
- Target state:
- Changed files:
- Contract impact:
- Verification command and environment:
- Evidence tier and verdict:
- Remaining risks:

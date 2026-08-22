# Text Analysis 调度退役场景

## 本地变更保护基线

- 变更开始分支：`codex/milestone-2b-three-gpu-deployment`。
- 变更开始 SHA：`56d42f5cc5e88f271935e0a5c99dadd54e0e07a6`。
- `text_analysis/` 是保留的非平台项目；开始时已有用户修改：`README.md` 以及未跟踪的
  `docker/Dockerfile`、`docker/README.md`。
- 排除 `__pycache__`、`.pytest_cache`、`logs` 和 `*.pyc` 后共有 101 个文件；按相对路径排序后
  对逐文件 SHA-256 清单再次计算 SHA-256，摘要为
  `96ea62765502e043443d44f9ba23d7eddf027a60ba0948eebf82b8e0428ca4e1`。
- 本变更不得修改、删除或覆盖上述项目文件。实施结束时必须使用同一算法复核摘要。

## 旧八算子 release 事实

- 只读核对时间：2026-08-21。
- 服务器：`192.168.29.11`；代码仍停留在
  `778515596b42123a3061daeb9a1c3bb446f1de1b`。
- release root：
  `/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/reports/`
  `milestone-2b/releases/v1.0_260812/778515596b42123a3061daeb9a1c3bb446f1de1b`。
- 旧 Canonical Controller 已无活动 PID，release tag 维护锁无持有者；24 项 new ledger 对应的
  算子容器均已停止，baseline ledger 为 0 项。
- 恢复证据为唯一 `0400`、root 所有、单硬链接的空 audit
  `existing-containers.jsonl.paused.jsonl.audit.cf9a3a2e57ff4789be1ea136bad4519e.jsonl`；
  原 `ocr-v6-amd` 保持 `Exited (143)`。
- 三个历史 Text Analysis 容器均为 `Exited (0)`；镜像
  `algorithm-text-analysis:v1.0_260812` 为 `amd64`，镜像 ID 为
  `sha256:9ed7659ad33343b2aa72d992a8e211a17db82c043633a541266e49ade03589e2`，revision 仍为旧 SHA。
- 本变更没有停止、删除、重标或清理上述容器、镜像、卷、模型、结果与历史报告。

## 数据兼容基线

- PostgreSQL 中 `PPT_KEYWORDS`：12 个状态 60 节点；另有 2 个状态 20 节点，其所属 PPT
  任务已经是状态 70。
- PostgreSQL 中 `COURSE_OVERVIEW`：6 个状态 60 节点；另有 1 个状态 20 节点，其所属 ASR
  任务已经是状态 70。
- 退役节点所属任务状态在 10 至 50 的行数为 0，因此当前数据满足切换预检条件；历史节点不得删除、
  补完成或改写。
- `operator_instances` 保留三个已注销的 `text_analysis` 审计事实；Redis 当前仅有 OCR 实例键，
  不存在 Text Analysis 实例、心跳、能力或租约键。

## 新目标合同

- 新 PPT DAG：`PPT_SLICE -> PPT_OCR`。
- 新 ASR DAG：`ASR_TRANSCRIPTION`。
- 当前平台拓扑：7 类算子、21 个实例、18 个 GPU 实例、3 个 CPU 实例、14 个配置解析进程。
- `text_analysis/` 源码保留，但平台不得构建、部署、注册、租赁或调用它。
- 历史八算子 release 和历史任务结果只作为历史事实，不作为七算子最终通过证据。

## 验证层级与剩余风险

当前仅完成本地工作区、远端旧 release、PostgreSQL、Redis、容器和镜像的只读基线核对。
DAG、注册、迁移、七算子部署权威、217 条反例、26 条压力/恢复、6 项人工复核和新 release
尚未在本记录中宣称通过；后续每达到一层再追加实际命令、数量和结论。

## 源码保护复核

- 复核时间：2026-08-21。
- 使用与变更开始时相同的排除规则再次统计，`text_analysis/` 仍有 101 个非缓存文件。
- 按相对路径排序的逐文件 SHA-256 清单摘要仍为
  `96ea62765502e043443d44f9ba23d7eddf027a60ba0948eebf82b8e0428ca4e1`。
- 摘要与变更前完全一致，因此本变更没有修改或删除该项目业务源码、接口、配置和测试，也没有覆盖
  变更开始时已有的用户 dirty 文件。

## 2026-08-21 本地实现与六层验证

- 平台完整测试为 `2738 passed, 3 skipped`；三个跳过项仅因本机没有 Canonical FaceRec 的
  `OPERATOR_REGISTRY_TOKEN`，不允许计入远端最终通过。四服务独立测试分别为 Control
  `25 passed`、Orchestrator `55 passed`、Vision `33 passed`、Online Gateway `20 passed`。
- 使用真实 PostgreSQL、Redis 与 Kafka 的集成测试为 `126 passed`，其中 Kafka 提交、Outbox、
  DAG 初始化、重复消息、恢复与终态专项为 `8 passed`；退役边界专项为 `54 passed`，Control
  活动退役节点预检为 `4 passed`。
- 七个算子的完整本机测试结果为：ASR Offline `58 passed`、ASR Online `22 passed`、OCR
  `175 passed`、FaceRec `54 passed`、ScreenDet `78 passed`、PPT Slice `100 passed`、VBas
  `75 passed`。这些结果证明当前源码合同，不替代远端三张 GPU 的真实推理证据。
- 变更文件 Ruff、`compileall`、四服务导入和 TOML 解析均通过；主平台、四服务与脚本 strict
  Mypy 检查 `143` 个源文件通过，部署脚本专项 `9` 个源文件通过。
- Compose 展开结果为基础设施 4 个、平台 bundle 8 个、算子 21 个；配置权威为 7 类算子和
  14 个独立解析进程。当前 catalog 为 217 条反例、26 条压力/恢复、`RET-001..010` 和 6 项
  B 级复核；Preflight 为 `301 passed`，Task 9 合同为 `247 passed`，current canonical 为
  `17 passed`。
- `retire-text-analysis-from-scheduling-platform`、`standardize-service-file-logging`、
  `unify-operator-capacity-leases-and-online-ocr`、`close-platform-runtime-and-harness-gaps` 均通过
  OpenSpec strict，`git diff --check` 通过。
- 已达到本地静态、单元、真实 PostgreSQL/Redis、真实 Kafka、服务运行合同和算子源码合同六层；
  尚未达到新 SHA 的远端七镜像、21 实例、18/18 GPU、3/3 CPU、真实业务泳道、243 条用例、
  Canonical 恢复与精确镜像清理证据。

## 2026-08-21 远端 Attempt 1：停止态历史容器预检缺口

- Attempt 1 SHA 为 `7cbfaf4f33127e85be1844c27fab79af992da490`，previous release 为
  `778515596b42123a3061daeb9a1c3bb446f1de1b`。模型资产、Harness Python 和七算子拓扑通过后，
  主机预检在镜像构建、容器启动和课程提交前失败。
- 失败原因是三个已退出的旧 Text Analysis 容器不再属于当前七算子 Compose，通用未知容器门禁
  因此把它们误判为需要人工确认的活动风险。现场没有新算子容器、活动维护锁或业务数据变更，
  四平台和基础设施保持健康。
- 修复只允许规范名称、旧 Compose 身份、`State.Status=exited` 且 `Running=false` 的三个固定
  历史容器通过。运行态、身份/名称漂移及其他未知算法容器继续失败关闭，不自动停止或删除。
- 修复后平台完整回归为 `2739 passed, 3 skipped, 27 warnings`；三个 skip 仍只因本机缺少
  Canonical FaceRec Token/容器，warnings 为多线程测试进程调用 `fork()` 的既有 Python 弃用提示。
- Attempt 1 只作为失败诊断证据；后续必须使用新的完整 SHA，并把本 release 作为立即前驱重跑
  全部 Canonical，不能在原 SHA 上覆盖结果。

## 2026-08-21 远端 Attempt 2：错误的前驱选择

- Attempt 2 SHA 为 `55059ff70b2a8486ca65a1721323cdd2297f8fea`。实际服务器容器清单已经
  通过修复后的生产校验，证明三个固定历史容器只在精确退出态被接受。
- Canonical 随后在镜像构建、维护快照和业务变更前拒绝启动，错误为
  `PREVIOUS_RELEASE_ROOT has no authoritative maintenance state`。原因是错误地把只产生预检文件和
  predecessor marker、但尚未建立权威 snapshot/audit 的 Attempt 1 当成前驱。
- 当前 release 继承只允许从拥有完整维护状态或可信 provenance 的 release 开始。下一 SHA 必须
  继续使用最近具备完整 snapshot、唯一恢复 audit 和算子账本的 `7785155...` 作为前驱；失败
  Attempt 1/2 均只保留为诊断证据，不删除、不覆盖也不再次使用相同 SHA。

## 2026-08-21 远端 Attempt 3：被本机忽略文件掩盖的 clean-clone 缺口

- Attempt 3 SHA 为 `fde5eef5516c0b2090fcca30229f93612fc8f949`，使用权威 `7785155...`
  前驱并成功建立维护快照；原 `ocr-v6-amd` 本来就是退出态，因此没有暂停运行中的业务容器。
- clean-clone 全量测试为 `1 failed, 2733 passed, 8 skipped`，唯一失败是 Git 中没有
  `facerec/config.toml`；本机工作树存在该 ignored 文件，因而此前的完整测试未暴露问题。
  同类审计进一步发现 `ocr/config.toml` 也未被跟踪。
- Canonical 在镜像构建、算子启动和课程提交前失败并完成 `restore: complete`。修复必须把两份
  当前平台算子的根默认配置纳入 Git，并用门禁证明 11 份目标根配置在 clean clone 中全部存在；
  `text_analysis/config.toml` 继续作为非平台项目配置排除。

## 2026-08-22 远端 Attempt 4：旧八算子账本投影缺口

- Attempt 4 SHA 为 `5a31ebd0fe95bdb378601189b2150132db3a0c73`。clean-clone 为
  `2735 passed, 8 skipped`，四服务、真实 PostgreSQL/Redis、真实 Kafka 和 14 进程配置权威均通过。
- 当前代码在继承旧八算子 release 的 24 项 new ledger 时，直接使用七算子 allowlist 校验历史容器，
  因此不能区分 21 个仍在平台范围内的实例与 3 个已退出的 Text Analysis 历史实例。Canonical 在当前
  算子构建、启动和课程提交前失败关闭并完成 `restore: complete`。
- 修复把历史 baseline/new 账本严格投影为当前 allowlist；只有三套固定身份、完整存在且处于
  Exited 状态的 Text Analysis 容器可以被排除，缺失、运行态、伪装或未知身份均继续拒绝。
- 本 Attempt 只保留为失败诊断，不满足远端七算子 release 的任何最终门禁。

## 2026-08-22 远端 Attempt 5：Compose orphan 重新进入当前快照

- Attempt 5 SHA 为 `b10751800bd4cf7c4e638ab76a36e9e71d795ad0`，立即前驱为 Attempt 4。
  clean-clone 为 `2740 passed, 8 skipped`；Control、Orchestrator、Vision 和 Online Gateway
  分别为 `25/55/33/20 passed`，真实 PostgreSQL/Redis 为 `69 passed`，真实 Kafka 为
  `12 passed`。七个算子镜像均成功构建并通过 `amd64`、完整 revision 和镜像身份门禁；未构建、
  重标或启动 Text Analysis。
- previous baseline/new 已正确投影为 21 个当前实例，但 Compose 的 `ps --all` 仍返回同 project 下
  三个停止态 Text Analysis orphan，导致未经投影的 24 项当前快照与 21 项 previous new 不一致。
  失败发生在启动当前 21 实例、业务提交、217 条反例、26 条压力/恢复和人工复核之前。
- 修复使当前快照先保留 Compose 完整集合，再通过同一个严格投影合同生成当前七算子快照。只有
  固定三套、身份完整且 Exited 的 orphan 可排除；未知容器、运行态退役容器、名称或 Compose 身份
  漂移仍失败关闭。相关聚焦、Task 9 和控制器/部署合同合计 `590 passed`，Ruff、strict Mypy 和
  `compileall` 均通过。
- Canonical 已完成 `restore: complete`；24 个历史算子容器保持 Exited，三个 Text Analysis 容器
  未启动、未删除，原 `ocr-v6-amd` 保持 Exited，PostgreSQL、Redis、Kafka、MongoDB 与四平台
  服务均 healthy。未执行 prune、`down -v`、卷、`/data/result`、历史 release 或镜像删除。
- Attempt 5 仍不得计入最终通过；下一次必须以包含本修复的新完整 SHA 和本 release 为立即前驱
  执行完整 Canonical。

## 2026-08-22 远端 Attempt 6：退役反例夹具仍引用 Text Analysis

- Attempt 6 SHA 为 `5c68595c83a17d3938b3e4f3a30be0744ed9d75c`，立即前驱为 Attempt 5。
  clean-clone `2740 passed, 8 skipped`，四服务、真实 PostgreSQL/Redis、真实 Kafka 和14进程
  配置权威全部通过；Attempt 5 阻断的 24→21 previous/current 账本投影已在真实 Compose 上通过。
- 七算子与四平台镜像均绑定本 SHA；四平台 healthy，21/21 实例注册、18/18 GPU 真实推理和
  物理卡/PID 归属、3/3 PPT CPU 真实切片、7/7 综合 Smoke 全部通过，Stage 4/5 终态为
  `CODEX_STAGE45_COMPLETE failures=0`。
- deployment 共执行 92 项，其中 91 项通过；`DEP-014` 失败为
  `checker reason does not contain required detail: CONFIG_PATH`。只读复核确认该 runner 仍用
  `text-analysis-cpu0` 构造错误配置，生产合同正确先拒绝未知退役算子，导致 checker 没有到达
  `CONFIG_PATH` 校验。修复只把变异目标改为当前 `ppt-slice-cpu0`，不放宽生产合同。
- 失败发生在三路课程媒体预检、业务 Campaign 和复核请求之前，未执行完整 217 条反例、26 条
  压力/恢复或 6 项 B 级复核，也未进入镜像清理。Canonical 自动输出 `restore: complete`；0/21
  baseline/new 账本完整，21 个当前算子与三个历史 Text Analysis 容器均 Exited，原
  `ocr-v6-amd` 保持 Exited，四平台和基础设施全部 healthy。唯一恢复审计为当前 UID、单链接、
  `0400` 的
  `existing-containers.jsonl.paused.jsonl.audit.0789d8284b7e4e228f1c0a27e2a63363.jsonl`；没有删除
  容器、镜像、卷、数据或历史证据。
- Attempt 6 仍是失败诊断证据；修复必须形成新 SHA，并以本 release 为立即前驱完整重跑。

## 2026-08-22 远端 Attempt 7：最终规格复审后主动中断

- Attempt 7 SHA 为 `88f9d6f17f7add1856b083b99d092118509d8375`，立即前驱为 Attempt 6。
  模型资产、报告初始化、七算子拓扑门禁、维护快照和暂停检查已通过；clean-clone pytest 仍在
  执行，尚未构建镜像或启动本轮算子。
- 并行复审确认 clean-clone 当前测试仍把 `text_analysis` 当作平台注册 runtime/Docker/requirements
  正向验证，部分活跃 Harness 文档仍指向旧八算子入口；B 级复核发布器也缺少当期 request/phase、
  整个 Git 工作区外路径和逐 case 摘要格式门禁。这些属于退役边界和最终证据合同遗漏。
- 向 Python 总控发送 `SIGINT` 后，控制器有界等待既有 Bash `EXIT` trap，最终输出
  `restore: complete`。唯一恢复审计为
  `existing-containers.jsonl.paused.jsonl.audit.3bd038a493d74aa0b1def93d0a379852.jsonl`，权限
  `0400`；维护锁已释放，当前算子运行数为0，原业务状态、四平台和四基础设施保持不变。
- 本 release 未执行业务泳道、217 条完整反例、26 条压力/恢复、6 项 B 级复核或镜像清理，只作为
  审计中断与精确恢复证据。合同修复后必须使用新 SHA，并以本 release 为立即前驱完整重跑。
- 修复后平台当前合同测试只覆盖七算子，并明确验证 `text_analysis/` 不进入拓扑、Compose 和受控
  TOML；当前 Harness 入口、证据矩阵、部署说明和旧离线设计已区分现行范围与历史事实。
- B 级发布器新增 request/phase、SHA、task、Git 外路径、逐案摘要、带时区时间和当前 release
  证据摘要门禁。定向验证为 `28 passed` 与 `37 passed`，平台全量为
  `2756 passed, 3 skipped, 27 warnings`；Ruff、strict Mypy、compileall、四项受影响 OpenSpec
  strict、静态退役排除和 `git diff --check` 通过。下一轮远端仍必须做到零 skip 和完整终态。

## 2026-08-22 远端 Attempt 8：复核材料路径示例冲突

- Attempt 8 SHA 为 `30a58482a91a76229e99663e0052237a5a81ada2`，立即前驱为 Attempt 7。
  运行进入 clean-clone，但尚未完成 pytest，也没有构建镜像、启动算子或进入业务 Campaign。
- 独立复核准备确认 Harness 建议的 `business/review-materials/{phase}.json` 不属于 canonical 报告
  白名单，照文档执行会在 `publish_json_once` 失败。决定不扩展报告面：逐案人工计数继续进入
  固定 `observed`，`evidence` 引用当前 release 已存在的 request、媒体预检或运行摘要；原始媒体
  和完整文本仍只在受限位置查看。
- 通过 Python 总控 `SIGINT` 有界中断后输出 `restore: complete`。唯一恢复审计为
  `existing-containers.jsonl.paused.jsonl.audit.4301cf0724bd4ad9ade85e0f89c1feb2.jsonl`，权限
  `0400`；维护锁释放，运行中算子为0，当前/历史算子均 Exited，四平台与四基础设施 healthy。
- 没有生成 offline/vision request、复核 input、artifact 或通过结论，也没有执行清理。本 release
  不计入最终通过；文档修复后必须以新 SHA 和本 release 为立即前驱完整重跑。

## 2026-08-22 远端 Attempt 9：课程媒体 URL 目录片段错误

- Attempt 9 SHA 为 `dc628302966ead17f51fb49d1e53f589ddc56690`，立即前驱为 Attempt 8。
  clean-clone 为 `2751 passed, 8 skipped`，四服务为 `25/55/33/20 passed`，真实
  PostgreSQL/Redis 为 `69 passed`，真实 Kafka 为 `12 passed`；退役静态排除为 0 违规，
  14 进程配置权威通过。
- 七个算子和四个平台镜像均绑定本 SHA 并通过 `amd64`、完整 revision 与精确镜像身份门禁；
  四平台 healthy，21/21 实例注册、18/18 GPU 真实推理与物理卡/PID 归属、3/3 PPT CPU
  真实切片及 7/7 综合 Smoke 全部通过。媒体门禁前执行的 75 条反例和 17 条压力/恢复基础用例
  也全部通过；这些部分证据只用于诊断，不补足最终目录。
- 三路课程媒体预检固定执行三轮，T/S/P 每轮均返回 HTTP `404`、声明长度与首块长度均为
  `153`，因此以 `media_probe_failed` 失败关闭。现场核对媒体服务器目录确认，本次 Canonical
  参数把实际存在的 `2025年9月12号17时10分` 目录误写为 `2025年9月12分`；正确目录仍包含
  `教师2.mp4`、`学生1.mp4` 和 `PPT.mp4`，不是媒体服务器、容器网络或探针实现故障。
- 失败发生在课程任务创建和 offline/vision 复核 request 发布前；没有生成 task、复核 input、
  artifact 或通过结论，Git 外复核索引保持空对象。下一 Attempt 必须先以正确三路 URL 通过
  独立只读探测，再从当前 release 作为立即前驱完整重跑，不复用本轮部分证据。
- Canonical 明确输出 `restore: complete`；唯一恢复审计为当前 UID、单硬链接、`0400` 的
  `existing-containers.jsonl.paused.jsonl.audit.9ae1777bcb5346928cb1aaff5651ded0.jsonl`。
  21 个当前算子与 3 个历史 Text Analysis 容器均为 Exited，原 `ocr-v6-amd` 保持原有 Exited
  状态，四平台和 PostgreSQL、Redis、Kafka、MongoDB 全部 healthy；未执行 prune、`down -v`、
  卷、数据、历史 release 或镜像删除。

## 2026-08-22 远端 Attempt 10：视觉媒体失败节点导致队列饥饿

- Attempt 10 SHA 为 `7fd453efe67ed8bcf7280e11a474488b4bedea58`，立即前驱为 Attempt 9。
  clean-clone `2751 passed, 8 skipped`，四服务 `25/55/33/20 passed`，真实
  PostgreSQL/Redis `69 passed`，真实 Kafka `12 passed`；七算子和四平台镜像、14 进程配置
  权威、21/21 注册、18/18 GPU 真实推理、3/3 PPT CPU Smoke 和 7/7 综合 Smoke 均通过。
- 75 条部署反例和 17 条基础压力/恢复用例通过；正确 T/S/P 地址的三轮媒体预检全部返回
  HTTP `206`、正声明长度与正首块长度，逐角色摘要跨轮稳定。真实 PPT 的 31 张切片与逐图 OCR
  已完成，真实 ASR 完整转写已持久化，两类任务均为状态60且没有退役节点。
- 视觉任务未进入执行。历史课程的 `STUDENT_BEHAVIOR_ANALYSIS` 节点 `186` 使用失效媒体地址，
  旧协调器在媒体准备异常后把已领取节点退回状态10；URGENT/FIFO 排序持续重领同一节点，attempt
  累计达到 `80543`，当前课程教师/学生节点的 attempt 均为0。Kafka 中四条课程命令已消费，但
  当前任务没有 visual command；Orchestrator 和 Vision readiness、VBas 实例与 GPU 均正常。
- 修复把领取节点从 `QUEUED` 转为 `RUNNING` 后再准备媒体；不可恢复的媒体准备错误进入状态70并
  聚合任务类型，Kafka 发布失败与取消仍按状态30恢复。定向视觉测试 `10 passed`、Orchestrator
  全量 `57 passed`、平台仓储/媒体定向 `50 passed`，Ruff、strict Mypy、`compileall` 和差异检查
  通过，不涉及迁移。
- 四泳道没有全部终态，Campaign 未生成 offline/vision request，外部复核索引、输入与 artifact
  均不存在，不能发布5项 offline 复核或 `VIS-025`。Canonical 受控 `SIGINT` 后输出
  `restore: complete`；唯一恢复审计
  `existing-containers.jsonl.paused.jsonl.audit.fa9746363b414d1ca2040f7b65fb3dbd.jsonl`
  为当前 UID、单硬链接、`0400`。21 个当前算子停止，3 个历史 Text Analysis 容器继续 Exited，
  原 `ocr-v6-amd` 保持 Exited，四平台和四基础设施 healthy；没有清理镜像、卷、结果或历史证据。
- 本 Attempt 不满足任务 9.4 至 9.7。修复提交后必须以新的完整 SHA、本 release 为立即前驱重跑，
  不能通过手工修改历史任务状态、热补丁或拼接本轮局部证据绕过。

## 2026-08-22 远端 Attempt 11：LOAD-015 错用全平台租约范围

- Attempt 11 SHA 为 `75e104a033a554c6184c2306630fa902e9b22279`，立即前驱为 Attempt 10。
  clean-clone 六层、真实 PostgreSQL/Redis/Kafka、七算子和四平台镜像、14 进程
  配置权威、21/21 注册、18/18 GPU 真实推理、3/3 PPT CPU Smoke 和 7/7 综合
  Smoke 均通过；75 条 deployment 反例全部通过。
- 基础压力/恢复用例为 16/17 通过，唯一失败为 `LOAD-015`。前序 `LOAD-013`
  仍有非 FaceRec 节点处于运行状态；`LOAD-015` 虽然的资源范围是
  `algorithm:operator-lease:facerec` 和 `recognize`，检查器却累加全平台
  `active_lease_count` 并要求为零，因而把其他能力的合法在途租约误判为
  FaceRec 污染。检查器在 Redis 重启前即失败关闭，这不是 Redis 世代隔离或
  FaceRec 租约释放回归。
- 修复仅把 `LOAD-015` 的前置、建立后与重启后三次租约计数限定到
  `operator_code=facerec`；其他算子有在途租约时可继续验证，FaceRec 自身初始
  非零、未建立唯一真实租约或重启后租约仍存在时仍失败关闭。相关回归
  `794 passed`，Ruff、strict Mypy、OpenSpec strict 与差异检查通过。
- 本轮未进入三路媒体预检、业务 Campaign、B 级复核、最终汇总或镜像清理。
  Canonical 输出 `restore: complete`，唯一 `0400` 恢复审计已生成，维护锁可获取，
  21 个当前算子和3个历史 Text Analysis 容器均为 Exited，原 `ocr-v6-amd`
  保持 Exited，四平台与四基础设施 healthy；未执行 prune、`down -v`、数据、
  证据或镜像删除。本 release 仅作真实失败与恢复证据，修复须以新完整 SHA
  继承该 release 并重跑全部 Canonical。

## 2026-08-22 远端 Attempt 12：Canonical 参数使用失效课程目录

- Attempt 12 SHA 为 `425a81ef9ef5219e987d116c7248fdaa0d36cd5a`，立即前驱为 Attempt 11。
  clean-clone、真实 PostgreSQL/Redis/Kafka、14 进程配置权威、七个算子和四个平台镜像、
  21/21 注册、18/18 GPU 真实推理、3/3 PPT CPU Smoke 与 7/7 综合 Smoke 全部通过。
- deployment 的75条反例和17条基础压力/恢复用例全部通过；修复后的 `LOAD-015` 成功验证
  FaceRec 租约 `0 -> 1 -> 0` 与 Redis 重启后实例重新注册，不再受其他算子合法在途租约影响。
  这些部分结果仅作本轮诊断证据，不能补足后续业务泳道和最终新 schema 验收。
- 三路课程媒体预检固定执行三轮，T/S/P 九次请求均返回 HTTP `404`，声明长度和首块长度均为
  `153`，因此以 `media_probe_failed` 失败关闭。只读枚举媒体源确认正确目录是
  `0912空中交通管理与签派_1223121_1223122_90020060,徐月芳,__2025年9月12号17时10分`，
  且其中存在 `教师2.mp4`、`学生1.mp4` 和 `PPT.mp4`；本轮启动参数遗漏目录片段，不是探针、
  Orchestrator 容器网络或媒体服务器不可用。
- 失败发生在任何课程任务创建之前，未进入 PPT/OCR、ASR-only、教师/学生视觉、在线图片、
  实时 ASR 或人物管理泳道，也未发布 offline/vision request、外部复核输入或 B 级结论。
  `preflight/course-media.json` 保持 write-once，禁止用同 SHA 覆盖为通过。
- Canonical 输出 `restore: complete`；唯一恢复审计
  `existing-containers.jsonl.paused.jsonl.audit.35af2684dcc04f2eb817db427fb41534.jsonl`
  为当前 UID、单硬链接、`0400`。21 个本轮算子均已停止，原 `ocr-v6-amd` 保持 Exited，
  四个平台与 PostgreSQL、Redis、Kafka、MongoDB 继续运行；维护锁可获取，未执行 prune、
  `down -v`、卷、数据、历史 release 或镜像删除。
- 下一 Attempt 必须先在 Orchestrator 容器外做只读 URL 状态确认，再以新 SHA 和本 release
  作为立即前驱完整重跑；不得复用本轮局部通过报告或覆盖失败媒体证据。

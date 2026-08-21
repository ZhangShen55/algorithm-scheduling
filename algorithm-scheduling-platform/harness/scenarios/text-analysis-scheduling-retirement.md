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
- deployment 反例已执行 75 项，74 项通过；`DEP-014` 失败为
  `checker reason does not contain required detail: CONFIG_PATH`。只读复核确认该 runner 仍用
  `text-analysis-cpu0` 构造错误配置，生产合同正确先拒绝未知退役算子，导致 checker 没有到达
  `CONFIG_PATH` 校验。修复只把变异目标改为当前 `ppt-slice-cpu0`，不放宽生产合同。
- 失败发生在业务 Campaign 和复核请求之前，未执行完整 217 条反例、26 条压力/恢复或 6 项
  B 级复核。Canonical 自动输出 `restore: complete`；0/21 baseline/new 账本完整，21 个当前算子
  与三个历史 Text Analysis 容器均 Exited，原 `ocr-v6-amd` 保持 Exited，四平台和基础设施全部
  healthy。没有删除容器、镜像、卷、数据或历史证据。
- Attempt 6 仍是失败诊断证据；修复必须形成新 SHA，并以本 release 为立即前驱完整重跑。

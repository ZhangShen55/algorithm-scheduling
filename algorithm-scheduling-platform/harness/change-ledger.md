# Change Ledger

## 2026-08-21 - `retire-text-analysis-from-scheduling-platform` 基线冻结

- 本变更从 `56d42f5cc5e88f271935e0a5c99dadd54e0e07a6` 开始；`text_analysis/` 的 101 个非缓存
  文件摘要冻结为 `96ea62765502e043443d44f9ba23d7eddf027a60ba0948eebf82b8e0428ca4e1`，其用户已有
  README 与 Docker 草稿保持只读。
- 只读复核 `192.168.29.11`：旧 `7785155...` Canonical 已无 PID或维护锁，24 个算子容器
  均已停止，存在唯一可信 `0400` 恢复 audit；原 `ocr-v6-amd` 保持退出态，未执行清理。
- PostgreSQL 保留 18 个完成和 3 个等待的历史 Text Analysis 节点；3 个等待节点所属任务均已失败，
  所属任务状态 10 至 50 的活动退役节点为 0。Redis 已无 Text Analysis 实例、心跳、能力或租约键；
  PostgreSQL 的三个已注销实例行继续作为历史审计事实保留。
- 当前证据只达到变更保护和旧 release/数据只读基线层级；七算子实现、本地六层验证和新远端 release
  尚未完成，不得使用旧八算子结果补足。

## 2026-08-21 - `retire-text-analysis-from-scheduling-platform` 文档范围收口

- 四份包含 Text Analysis、八算子或退役节点的旧 Harness 场景仅追加后续废止说明；旧命令、数量、
  结果、SHA 和 release 路径保持不可变，不能作为当前七算子最终验收证据。
- 根与平台 `AGENTS.md` 的长期合同收敛为七个当前算子、21 个实例、18 个 GPU 实例、3 个 CPU
  PPT Slice 实例和 14 个配置解析进程；`text_analysis/` 只作为非平台项目保留。
- 当前新任务 DAG 固定为 `PPT_SLICE -> PPT_OCR` 和 `ASR_TRANSCRIPTION`。历史任务中的
  `PPT_KEYWORDS`、`COURSE_OVERVIEW` 及历史 `operator_code=text_analysis` 仍可查询，但平台不得
  再创建、注册、路由、租赁或调用这些节点和能力。
- 总体设计保留既有架构图并新增带日期与版本号的七算子部署图和当前 DAG 图；A 服务、数据库、
  部署及四服务 README 同步当前边界。本条只记录文档/治理层变更，不宣称代码、Compose、远端
  七算子 release、217 条反例、26 条压力/恢复或 6 项人工复核已经通过。

## 2026-08-21 - `retire-text-analysis-from-scheduling-platform` 本地实现闭环

- 新 PPT DAG 已收敛为 `PPT_SLICE -> PPT_OCR`，新 ASR DAG 只保留
  `ASR_TRANSCRIPTION`；Control 拒绝新的 `text_analysis` 注册，历史 PostgreSQL 节点和审计
  字符串继续可查，活动退役节点切换预检失败关闭。
- 平台完整测试为 `2738 passed, 3 skipped`；三个跳过项只因本机缺少 Canonical FaceRec
  注册 Token，远端最终验收不得跳过。真实 PostgreSQL/Redis/Kafka 集成为 `126 passed`，
  Kafka 闭环专项 `8 passed`，退役边界专项 `54 passed`，Control 预检 `4 passed`。
- 七算子本机完整测试分别为 `58/22/175/54/78/100/75 passed`。Ruff、`compileall`、四服务
  导入/TOML、143 个主平台 strict Mypy 源文件和 9 个部署脚本 strict Mypy 源文件全部通过。
- 新权威已固定为 7 类算子、21 个实例、18 个 GPU 实例、3 个 CPU 实例、14 个配置解析进程；
  当前用例目录为 217 条反例、26 条压力/恢复、10 条 RET 和 6 项 B 级复核。
- 本记录只确认本地六层门禁和实现闭环。新 SHA 的远端七镜像构建、21/21 注册、18/18 GPU、
  3/3 CPU、七条真实泳道、243 条用例、恢复和精确清理仍未执行，旧八算子证据不得补足。

## 2026-08-21 - `standardize-service-file-logging` 十一进程轮转补充证据

- 11 个独立 Python 进程在隔离临时目录中验证写前轮转、一日过期清理、未过期归档保留和实例
  目录隔离，终态为 `{"processes": 11, "status": "PASS"}`。
- 本地已完成 11 项导入/编译、四服务回归、静态门禁、Compose/配置权威和轮转进程验证；七算子
  真实模型推理及代表性 HTTP/WebSocket 敏感哨兵仍保留为远端同 SHA 验收项，不提前勾选。

## 2026-08-21 - 七算子远端 Attempt 1 停止态历史容器缺口

- SHA `7cbfaf4f33127e85be1844c27fab79af992da490` 在模型、Harness Python 与七算子拓扑通过后，
  主机预检因三个旧 Text Analysis 容器已退出但不属于当前 Compose 而失败；尚未构建新镜像、
  启动算子或提交课程任务，维护锁已释放，四平台与基础设施保持原状态。
- 修复将三个固定旧服务的精确 Compose 身份、规范名称和退出态识别为只读历史资产；任何运行态、
  名称漂移或其他未知算法容器继续失败关闭。修复后平台完整回归为
  `2739 passed, 3 skipped, 27 warnings`；本 attempt 不计入最终远端验收。

## 2026-08-21 - 七算子远端 Attempt 2 前驱选择失败

- SHA `55059ff70b2a8486ca65a1721323cdd2297f8fea` 已通过真实服务器容器清单校验，但误把没有
  权威 snapshot/audit 的 Attempt 1 目录作为 `PREVIOUS_RELEASE_ROOT`，Canonical 在镜像构建和
  维护快照前以 `PREVIOUS_RELEASE_ROOT has no authoritative maintenance state` 失败关闭。
- 现场无新算子、无活动维护锁、无课程提交或业务数据变更。下一 SHA 继续以具有完整维护状态和
  唯一恢复 audit 的 `7785155...` 为可信前驱；两个失败 attempt 仅作为诊断证据保留。

## 2026-08-21 - 七算子远端 Attempt 3 clean-clone 根配置缺口

- SHA `fde5eef5516c0b2090fcca30229f93612fc8f949` 已通过主机预检并建立受控维护快照，但
  clean-clone 全量测试为 `1 failed, 2733 passed, 8 skipped`：Git 中没有被本机 ignore 文件
  掩盖的 `facerec/config.toml`，同类检查还确认 `ocr/config.toml` 未被跟踪。
- Canonical 在镜像构建、算子启动和课程提交前完成 `restore: complete`。修复把两份当前算子
  根默认配置纳入 Git，并增加 11 项根配置必须跟踪的回归；`text_analysis` 配置继续排除。

## 2026-08-22 - 七算子远端 Attempt 4 旧账本拓扑投影缺口

- SHA `5a31ebd0fe95bdb378601189b2150132db3a0c73` 的 clean-clone 为
  `2735 passed, 8 skipped`，四服务为 `25/55/33/20 passed`，真实 PostgreSQL/Redis 为
  `69 passed`，真实 Kafka 为 `12 passed`；14 进程配置权威也已通过。
- Canonical 在构建和启动当前算子前继承旧八算子 release 的完整账本时失败：历史 new ledger
  含 24 个容器，而当前权威 allowlist 只有 21 个服务，旧逻辑要求两者直接按当前拓扑校验，无法
  表达三个已退役 Text Analysis 容器作为历史事实继续存在。
- 修复使用固定退役身份把旧 baseline/new 账本严格投影到当前七算子拓扑；三套身份必须完整、
  规范且处于退出态，缺失、运行态、伪装或未知容器继续失败关闭。Canonical 已输出
  `restore: complete`，原 `ocr-v6-amd`、基础设施和四平台服务恢复到执行前状态。
- Attempt 4 只证明 clean-clone、集成测试、配置权威和账本投影缺口，不计入 21 实例或最终 release
  通过证据；修复以新 SHA 重跑。

## 2026-08-22 - 七算子远端 Attempt 5 Compose orphan 当前快照缺口

- SHA `b10751800bd4cf7c4e638ab76a36e9e71d795ad0` 的 clean-clone 为
  `2740 passed, 8 skipped`，四服务为 `25/55/33/20 passed`，真实 PostgreSQL/Redis 为
  `69 passed`，真实 Kafka 为 `12 passed`；七个算子镜像全部构建成功并通过 `amd64`、完整
  revision 和精确镜像身份门禁，没有构建或重标 Text Analysis。
- 失败发生在启动 21 个当前算子实例之前。previous baseline/new 已正确投影为七算子集合，但
  `docker compose ps --all -q` 仍返回同 project 下三个已停止的 Text Analysis orphan；未经投影的
  24 项当前快照与 21 项 previous new 比较，触发
  `current - previous baseline（当前拓扑投影后）必须与 previous new ledger（当前拓扑投影后）精确一致`。
- 当前快照修复先保留 Compose 返回的完整集合，再复用既有账本投影合同，只排除固定身份完整且
  处于 Exited 状态的三套历史 Text Analysis orphan；未知容器、运行态退役容器及名称或 Compose
  身份漂移继续失败关闭。聚焦、Task 9 与控制器/部署合同共 `590 passed`，Ruff、strict Mypy 和
  `compileall` 通过。
- Canonical 已输出 `restore: complete`；24 个历史算子容器均保持 Exited，三个 Text Analysis
  容器未启动、未删除，原 `ocr-v6-amd` 保持 Exited，四平台和基础设施均 healthy。未提交业务
  任务、未生成复核请求、未执行 prune、`down -v`、卷/数据/历史 release 或镜像清理。
- Attempt 5 仍是失败诊断证据；修复提交后必须使用新 SHA，并以本 release 为立即前驱重跑完整
  Canonical。

## 2026-08-22 - 七算子远端 Attempt 6 退役反例夹具漂移

- SHA `5c68595c83a17d3938b3e4f3a30be0744ed9d75c` 首次真实越过 24→21 账本投影门禁。
  clean-clone 为 `2740 passed, 8 skipped`，四服务为 `25/55/33/20 passed`，真实
  PostgreSQL/Redis 为 `69 passed`，真实 Kafka 为 `12 passed`，14 进程配置权威通过。
- 七个算子和四个平台镜像均以本 SHA 构建并通过 `amd64`、revision 与精确镜像身份门禁；四平台
  healthy，21/21 实例注册、18/18 GPU 真实推理与 GPU/PID 归属、3/3 PPT CPU 真实切片以及
  7/7 综合 Smoke 全部通过，终态为 `CODEX_STAGE45_COMPLETE failures=0`。
- deployment 批次已产生 92 份执行记录，其中 91 通过、`DEP-014` 失败。其受控错误配置仍使用
  已退役的 `text-analysis-cpu0`，生产合同先返回“未知算子”，checker 因而无法观察要求的
  `CONFIG_PATH` 细节。该问题属于当前反例夹具与七算子范围漂移，不是生产配置被错误接受。
- 修复把 `DEP-014` 的变异目标改为现役 CPU 算子 `ppt-slice-cpu0`，并增加不替换生产校验器的
  直接回归；保留原案例 ID、非法配置语义和失败关闭要求。
- 本 Attempt 未进入三路课程媒体预检、真实课程业务 Campaign、217 条完整反例、26 条完整
  压力/恢复或 6 项 B 级复核，也未创建外部复核索引。Canonical 已输出 `restore: complete`；
  baseline/new 为 0/21，21 个现役算子和三个退役 Text Analysis 容器均为 Exited，原
  `ocr-v6-amd` 保持 Exited，四平台与四基础设施均 healthy。唯一恢复审计为当前 UID、单链接、
  `0400` 的 `existing-containers.jsonl.paused.jsonl.audit.0789d8284b7e4e228f1c0a27e2a63363.jsonl`；
  未执行 prune、`down -v`、卷、数据、历史 release 或镜像删除。
- Attempt 6 只证明上述已通过边界，不满足 OpenSpec `14.3-14.7` 的完整验收；修复后必须使用
  新 SHA 并以本 release 为立即前驱重跑 Canonical。

## 2026-08-22 - 七算子远端 Attempt 7 审计主动中断

- SHA `88f9d6f17f7add1856b083b99d092118509d8375` 已通过模型资产校验、报告目录初始化、当前拓扑
  `7/21/18/3/14/7` 门禁、维护快照和受控暂停检查；clean-clone 全量 pytest 尚未结束，未进入
  镜像构建、算子启动、业务 Campaign、反例/压力、B 级复核或镜像清理。
- 并行最终规格复审发现三类当前合同漂移：平台 clean-clone 仍正向验证 `text_analysis` 注册
  runtime/Docker/requirements；活跃 Verification/证据矩阵仍把八算子入口写作当前命令；B 级
  复核发布器未强制当期 request/phase、整个 Git 工作区外路径和逐 case 摘要格式。
- 为避免错误 SHA 继续形成发布证据，向 Python Canonical 总控发送 `SIGINT`。总控等待 Bash
  `EXIT` trap 完成后退出，终态明确输出 `restore: complete`；唯一恢复审计为当前 UID、单链接、
  `0400` 的
  `existing-containers.jsonl.paused.jsonl.audit.3bd038a493d74aa0b1def93d0a379852.jsonl`。
- 恢复后 release-tag 维护锁可重新获取，当前算子运行数为0，原 `ocr-v6-amd` 保持 Exited，
  四个平台服务和 PostgreSQL、Redis、Kafka、MongoDB 全部 healthy。未执行 prune、`down -v`、
  卷、数据、镜像或历史 release 删除。
- 本 release 只证明中断恢复合同，不计入 OpenSpec 远端通过。修复必须形成新完整 SHA，并以本
  release 为立即前驱重新执行全部 Canonical。
- 修复后当前平台测试只正向验证拓扑权威中的七个算子，并单独断言 `text_analysis/` 源码保留但
  不进入 Compose/部署配置；Verification、证据矩阵、报告 README、部署 README 和旧离线设计
  已区分当前七算子入口与历史八算子事实。
- B 级发布器现按 request phase 精确绑定 SHA、任务、外部索引和完整 case 集合；输入与索引均
  排除整个 Git 工作区和 release，校验可追溯 reviewer、带时区时间、六项固定 `observed` schema
  及 `release:<path>#sha256:<digest>` 当前证据引用。
- 本地验证：B 级复核与业务 Campaign 定向 `28 passed`，七算子入口/配置/Harness 定向
  `37 passed`，平台全量 `2756 passed, 3 skipped, 27 warnings`。3 个 skip 仅因本机没有远端
  Canonical FaceRec Token/容器；27 个 warning 为既有多线程进程中 `fork()` 的 Python 弃用提示。
  Ruff、strict Mypy、compileall、四项受影响 OpenSpec strict、退役静态排除和 `git diff --check`
  全部通过。

## 2026-08-21 - `standardize-service-file-logging` 本地实施与验证

- 本变更基于 `778515596b42123a3061daeb9a1c3bb446f1de1b` 开始，目标是七个当前算子和四个平台服务；
  `text_analysis/` 保持只读，不纳入日志实现、镜像或默认日志 override。当前工作区仍有其他用户变更，
  最终 SHA 尚未冻结。
- 已完成共享文件日志实现：项目根 `logs/{instance_id}/application.log`、文件/stdout 双输出、
  100 MiB 单文件上限、七日归档清理、实例隔离、结构化字段、脱敏和 Uvicorn handler 幂等接管；
  平台服务通过 `platform_common.logging` 复用同一合同。
- 已完成 11 个根配置、7 个当前算子部署 TOML、Dockerfile 日志目录创建、可选
  `docker-compose.logs.yml`、README/AGENTS/部署边界和 Harness 场景更新。默认 Compose 不挂载宿主机日志，
  显式 override 才挂载 `/data/logs/algorithm-scheduling/{service}/{instance_id}`。
- `deploy/scripts/preflight host` 已支持可选 `LOG_ROOT`：未设置时完全跳过宿主机日志目录；设置后
  仅创建并验证非符号链接、当前身份归属、可写性和磁盘余量，不接触 `/data/result`。
- 本地证据：提交前日志/平台/配置/Compose 聚焦回归 `107 passed`，ASR Offline 运行配置
  `4/4`、OCR 完整套件 `175 passed`、FaceRec 日志配置 `1 passed`；敏感日志脚本和三份 Compose
  展开均通过。平台 `.venv` 的 Ruff、strict Mypy（141 个源文件）和 `compileall` 均通过。
  共享 wheel 仅向七算子构建和分发，SHA-256 为
  `ff489dc4cd207cb4903dd1679a55e202349cb908fffdeb7ced12069b9ee869c8`；其在 Python 3.11.13
  与 3.10.19 隔离导入通过，`asr`/`facerecapi` 的 `pip check` 通过。
- 修复了一个真实兼容问题：共享日志实现不再直接依赖 Python 3.11 的 `datetime.UTC`，改用
  `timezone.utc`；同时静态敏感检查改为 AST 参数判断，避免把日志文案中的 `embedding` 误报为向量数据。
- 尚未完成：11 项真实推理/真实进程轮转与容器重建、远端构建、完整 2B 业务泳道和最终 SHA 冻结；
  这些必须在 `retire-text-analysis-from-scheduling-platform` 本地完成后，以同一最终 SHA 统一执行，
  本条不回写旧 release 的失败/通过结论。

## 2026-08-20 - `ea39759` Canonical LOAD-011 平台运行时稳定门禁修正

- 失败 release：Git SHA `ea39759ad8abb7d970bef386d1f1de0dd0391c71`，证据目录为
  `/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/v1.0_260812/ea39759ad8abb7d970bef386d1f1de0dd0391c71`。
  clean-clone 六层门禁、16 进程配置权威、八类镜像/revision、PostgreSQL `0006`、
  四个平台服务、24 实例注册、18/18 GPU 真实推理、3 个 PPT Slice 完整 P 视频切片、
  3 个 Text Analysis 真实 Smoke 及全 24 实例综合 Smoke 均通过；阶段 4/5 明确终态为
  `CODEX_STAGE45_COMPLETE failures=0`。
- 217 条反例和 26 条压力用例均实际执行；唯一失败为 `LOAD-011 scoped task has no DAG`。
  现场时间线显示 Orchestrator 自 `2026-08-20T08:15:02Z` 起持续
  `/ops/readiness=503`。用例停止三个 ASR Offline 实例后提交的课程任务已经写入任务事实和
  Outbox，但后台运行时未就绪，因而没有消费命令并初始化 DAG。该失败不是 ASR 容器未恢复，
  而是 Stage45 与 deployment 用例之间缺少平台运行时重新稳定门禁。
- Canonical 已按精确 24 项 new ledger 停止本轮算子并完成 `restore: complete`。原业务恢复事实
  由唯一 `0400`、当前 UID、单硬链接 audit
  `existing-containers.jsonl.paused.jsonl.audit.8e0f6170b42e4b75817da9b8a373b07d.jsonl`
  固化；旧镜像未删除，offline/vision/online Campaign 和 B 级复核均未开始，因此本 release
  不能完成 OpenSpec `12.9` 或 `14.1/14.3-14.7`。
- 修正后 8A.7 在 Stage45 成功后、deployment 用例开始前检查 Orchestrator readiness；仅在
  未就绪时精确重启 `orchestrator-service`，等待其健康，再执行绑定当前 SHA 的 runtime
  preflight。Control、Vision、Online、PostgreSQL、Redis、Kafka、MongoDB 均不得随之重启；
  readiness、精确重启、健康等待和 preflight 任一步失败都必须在用例前失败关闭。
- 新 SHA 必须把本 release 作为立即前驱完整重跑 Canonical。只有 deployment、四类业务
  Campaign、8 项 B 级复核、容量/恢复门禁、最终报告和精确旧镜像清理全部通过，才允许完成
  剩余 OpenSpec 任务。

## 2026-08-20 - `7111d7d` Canonical LOAD-015 幂等释放契约漂移修正

- 失败 release：Git SHA `7111d7dd2557222db111a9d6bb912cc9dae35947`，证据目录为
  `/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/v1.0_260812/7111d7dd2557222db111a9d6bb912cc9dae35947`。
  八类算子与四个平台镜像构建、revision 校验、替换和运行预检通过，24 个实例完成注册；
  18/18 GPU 真实推理及 CUDA PID/cgroup 归属、6/6 CPU Smoke、8/8 算子 full Smoke 和
  PPT 三实例真实长视频切片均通过，阶段 4/5 终态为 `CODEX_STAGE45_COMPLETE failures=0`。
- 93 条 deployment 用例均生成结构化记录，唯一失败为 `LOAD-015`，最终终态为
  `CODEX_8A3_TERMINAL stage45_failures=0 deployment_status=1`。阶段 6 已完成安全恢复：
  24 个测试算子均停止、GPU 无残留计算进程，原业务状态恢复，四个平台服务与
  PostgreSQL、Redis、Kafka、MongoDB 均健康，维护锁已释放；本轮未执行旧镜像清理。
- 根因是 checker 只读取释放接口的 HTTP 状态码并要求 `404`。当前 Control Service 的正式幂等
  契约对已释放/失效租约返回 HTTP `200` 和业务状态 `ALREADY_RELEASED`，因此 checker 把
  正确响应误判为旧租约仍存活。生产 Redis 世代隔离没有回归：远端使用独立 key 前缀建立
  真实旧世代租约并重启 Redis，`run_id` 从
  `0d72f0d21ecb617887133d738046c5295172326f` 变为
  `2acd6d42ccfad60c7a964e90764a01a94f1572e0`，释放 Lua 返回 `0`，租约 hash 和 ZSET
  活跃计数均清零；测试前缀随后被精确删除，四个平台和基础设施恢复健康。
- 修正后 `_release_case_lease` 同时校验 HTTP 与白名单业务正文：只有
  `ALREADY_RELEASED` 证明重启前租约已失效，`RELEASED` 必须继续失败关闭；正文类型、
  `lease_id` 或业务状态异常也必须失败。兼容旧实现的 HTTP `404`，并在结构化证据中分别记录
  `lease_release_http_status` 和 `lease_release_status`。本机 LOAD runner 为 `101 passed`，
  部署脚本与 8A.3 控制器组合为 `305 passed`，Harness/catalog 为 `17 passed`；Ruff、strict
  Mypy、compileall、OpenSpec strict 和 `git diff --check` 均通过。新 SHA 必须以本 release
  作为 `PREVIOUS_RELEASE_ROOT` 建立新的不可变 Canonical 证据。

## 2026-08-20 - `bfee34e` Canonical REG-020 规格漂移与 TTL 回收门禁修正

- 失败 release：Git SHA `bfee34e82cddcf5d635b2cb009d1d6e3ef03e114`，证据目录为
  `/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/v1.0_260812/bfee34e82cddcf5d635b2cb009d1d6e3ef03e114`。
  八类算子和四平台镜像已完成构建/revision 校验与替换，24 个算子实例已注册；
  18/18 GPU 真实推理和 CUDA PID/cgroup 归属、6/6 CPU Smoke、8/8 算子 full Smoke
  以及 PPT 三实例真实长视频切片均通过，阶段 4/5 终态为
  `CODEX_STAGE45_COMPLETE failures=0`。
- 部署用例在第 76 条 `REG-020` 停止；前 75 条为 `75 passed`。旧 checker 在 1 秒
  租约 TTL 过期后仍要求 `reported_inflight=1` 阻止新租约，这与本变更已确认的
  “活跃租约是分发占用唯一权威，心跳只观测”直接矛盾。若心跳能阻止 TTL 回收，
  调用方崩溃后将无法释放容量。
- 修正后 `REG-020` 验证调用方停止续租时：旧租约不可续租、活跃租约数清零、
  `reported_inflight` 差异仍可观测、新工作可取得回收的槽位。真实长调用不重叠由调用方
  持续续租，以及续租失败后终止本次调用来保证；已有跨 TTL 调用回归继续覆盖该边界。
- 本机回归：真实 Redis Registry `22 passed`，Foundation runner `511 passed, 3 skipped`，
  catalog `12 passed`，Ruff 通过。3 个 skip 仅因本机未提供 Canonical FaceRec Token。
  `bfee34e...` 不满足部署终态，本轮未删除旧镜像；新 SHA 必须以该 release 作为
  `PREVIOUS_RELEASE_ROOT` 建立新的不可变 Canonical 证据。

## 2026-08-20 - 算子账本在 direct maintenance 中断窗口的只读恢复

- 现场失败：`2009d7b8ffc9ad8be06dbbbfeda28a6e8782ad90` 已完成八类镜像构建，但阶段 3 的
  `resolve-operator-ledgers` 经 maintenance provenance 到达
  `b0012b513cdb0548d9ff37b2b5da98f057a76859` 后失败；该 release 已有合法 direct
  snapshot/paused 和 `0400` predecessor marker，却在 `baseline/new` 初始化前中断。
- 只读核验：predecessor marker 精确指向具有完整账本的
  `1aa5da672f75adfa7aea5f767bc91e9ac4889cce`；其空 baseline 与 24 项 new 账本计算出的
  SHA-256 为 `b96ec6d4c0d78434461d2c438206fd26258b2f8bdf6a19bb6bd41c5050302c7b`，与服务器当前
  24 个 `algorithm-operators` 完整容器 ID 排序清单字节级一致。
- 修复边界：resolver 只在候选具有合法 direct maintenance、缺少完整账本并存在当前 UID
  所有、单链接、`0400` predecessor marker 时沿 marker 查找同 tag 前驱；缺 marker、partial、
  非法 marker、环或最终无完整账本仍失败关闭。阶段 3 继续强制
  `current - resolved baseline == resolved new`，且不修改任何历史 release 证据。
- 当前证据：新增现场等价正向回归和 direct 无 marker 反例；远端 canonical 尚未以修复后的新
  Git SHA 执行，因此本条不完成 OpenSpec `9.5/12.9/14.1-14.7`。

## 2026-08-20 - 统一容量发布首次 Canonical 构建失败与构建期配置修复

- 失败 release：Git SHA `b0012b513cdb0548d9ff37b2b5da98f057a76859`，证据目录为
  `/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/v1.0_260812/b0012b513cdb0548d9ff37b2b5da98f057a76859`。
  预检、模型清单、旧镜像快照和 registry wheel `0.2.0` 分发通过；
  `seacraft-asr-offline:v1.0_260812` 完成构建并取得目标 revision。
- 失败点：ASR Online Dockerfile 在运行层执行构建期 `from app.main import app`，但该镜像按部署
  合同不内置本地 `config.toml`，运行配置只由 Compose 挂载到 `/config.toml`，因此导入时以
  `ValueError: 算子配置文件不存在: /app/config.toml` 失败。该错误发生在容器替换、24 实例注册、
  GPU/Smoke 和 deployment 用例之前，不能完成 OpenSpec `9.5/12.9/14.2/14.7`。
- 修复：构建期导入检查在同一 `RUN` 层创建并删除临时空 TOML，通过 `CONFIG_PATH` 显式使用；
  不复制、重包含或持久化本地运行配置，Compose 运行时只读挂载合同不变。ASR Online 项目测试和
  平台镜像合同测试同时约束临时配置、显式 `CONFIG_PATH`、清理和禁止复制本地配置。
- 同类审计：ScreenDet 的 Cython 构建层也会在正式配置尚未挂载时导入 `app.main`。该问题尚未在
  Canonical 中实际触发，因为构建在 ASR Online 阶段已经停止；现已采用相同的临时配置边界修复，
  并由 ScreenDet 项目测试和平台镜像合同测试覆盖，避免重跑时在后续 profile 重复失败。
- 恢复与安全：Canonical 失败后维护锁已释放，PostgreSQL、Redis、Kafka、MongoDB 和四个平台服务
  均恢复 healthy；未启动 24 个新算子容器，未执行旧镜像清理，`/data/course`、`/data/result`、
  模型和历史证据未修改。修复必须以新的完整 Git SHA 建立另一不可变 release 重跑。

## 2026-08-19 - 统一容量租约与在线 OCR apply 中间收口

- 公共注册包统一严格解析八算子 `[platform]` 和 `[runtime].require_gpu`，容量只接受正整数；
  八份根/部署 TOML、Compose 24 实例和注册预检已切换到 TOML 权威，旧平台/GPU同义环境变量
  已移除，实例身份、Token、服务 URL、GPU 绑定和单 worker 继续由 Compose 管理。
- Redis 活跃租约成为分发占用权威，`reported_inflight` 只做差异观测；Control 增加可选工作
  上下文、补绑和实例活跃租约查询，不增加逐租约 PostgreSQL 写入。
- Orchestrator、Vision Orchestrator 和 Online Gateway 已按真实工作单元申请、续租和释放容量；
  PPT OCR/关键词按单图租赁并保留部分结果，在线网关新增单图 OCR 和 72/50 MiB 双边界。
- 本地中间证据：配置/Compose/预检/wheel 合同 `118 passed`，PPT 工作项恢复 `4 passed`，八算子
  项目测试分别为 `22/58/54/175/78/100/75/25 passed`。这些证据尚未绑定最终 Git SHA，不能
  替代四服务运行、真实跨服务泳道、24 实例重建和精确旧镜像清理。
- 平台完整回归修复 4 个部署替身漂移后复跑终态为 `2579 passed、3 skipped、27 warnings`；
  3 个跳过项仅因本地未提供 canonical FaceRec 集成所需的 `OPERATOR_REGISTRY_TOKEN`，不是功能
  失败。Ruff、目标范围 strict Mypy（106 个源文件）、变更部署脚本 strict Mypy、compileall、
  Harness 一致性、OpenSpec strict 与 `git diff --check` 均通过。
- `docs/运维可视化平台详细设计文档-v1.md` 是用户未跟踪且明确排除的草稿，不属于本变更的
  提交或验收对象；OpenSpec `10.3` 已收敛为只核对受版本控制的当前总体设计并完成。

## 2026-08-19 - 统一算子容量、可归属租约与在线 OCR Harness 规划基线

- 新增独立 Harness 场景 `scenarios/unified-operator-capacity-leases-and-online-ocr.md`，对应
  OpenSpec `unify-operator-capacity-leases-and-online-ocr`。当前 proposal、design、三份规格和
  tasks 为 `4/4` 完整，严格校验通过；88 项实施任务均保持未勾选。
- 本记录以用户修改后的规格为准：`max_concurrent_requests/declared_capacity` 只允许正整数，
  不再保留 `-1`；八算子默认值仍为 `10/4/128/256/128/10/128/256`。
- 增补配置归属门禁：八算子从 TOML `[platform]` 读取注册开关、Control Service 地址、心跳和
  容量，从 `[runtime].require_gpu` 读取 GPU 强制检查；根 TOML 使用本地安全默认值，部署 TOML
  启用注册。Compose 只保留 Token、实例身份、服务 URL、物理 GPU/可见设备、启动和资源事实，
  并用 YAML anchors 收敛重复项；源文件和 `docker compose config` 展开结果都必须验证。
- 增补同步 HTTP 生命周期门禁：Orchestrator、Vision Orchestrator 和 Online Gateway 的调用必须
  使用有限硬超时；可能跨越单次 TTL 时周期续租同一个租约，完成、失败、超时或取消后释放，
  调用方失联后由 TTL 回收。
- 增补图片边界门禁：Online Gateway 请求体上限 72 MiB、Base64 解码图片上限 50 MiB，OCR
  `image_max_bytes` 同步为 50 MiB；超限请求必须在网关申请租约前或 OCR 推理前失败。
- 增补 2B 存储门禁：最终 SHA 新镜像完成 revision、容器替换、健康、24 实例注册和 Smoke 后，
  只按精确 ID 删除无容器引用且身份可证明的旧平台/算子镜像；禁止强制删除、宽泛 prune，以及
  删除基础/基础设施/原业务镜像、模型、数据和历史证据。当前尚无删除运行证据。
- 新场景固定规格到证据矩阵、证据目录、脱敏规则和完成门禁；`architecture-review.md` 新增
  `DEC-025`，当前结论为“待验证”。本条没有业务实现或运行证据，不完成 OpenSpec 10.4，
  也不改变既有 `DEC-022/DEC-024` 和里程碑 2B 结论。

## 2026-08-19 - 8A.3 `1aa5da67` 第三轮正式通过

- 正式 release：Git SHA 为 `1aa5da672f75adfa7aea5f767bc91e9ac4889cce`，不可变证据目录为
  `/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/v1.0_260812/1aa5da672f75adfa7aea5f767bc91e9ac4889cce`。唯一入口
  `python3 deploy/scripts/run_milestone_2b_8a3.py` 退出码为 0，并同时产生
  `CODEX_STAGE45_COMPLETE failures=0` 与
  `CODEX_8A3_TERMINAL stage45_failures=0 deployment_status=0`。
- FaceRec 继续使用 x86 Docker 镜像和 NVIDIA Container Runtime，不依赖服务器 Conda。
  `facerec-gpu0/1/2` 均完成真实人物创建、识别、`save_person_photo=false` 和人物清理；三份
  GPU evidence 均为 `PASS`，停止后 CUDA PID 残留证据也均为 `PASS`。
- 18/18 个 GPU 实例完成真实推理、精确容器/CUDA PID 归属、停止、PID 消失、重启和注册恢复；
  18 份 running evidence 与 18 份 stopped evidence 全为 `PASS`。PPT Slice 与 Text Analysis
  的 6/6 CPU 实例 Smoke 通过，ASR Offline/Online、OCR、VBas、FaceRec、ScreenDet、PPT Slice、
  Text Analysis 的 8/8 full Smoke 全为真实 `PASS`。
- catalog 中 `phase=deployment` 的 93 条用例全部生成结构化执行记录：93/93 状态为“通过”、
  `mock=false`、Git SHA 一致。`INF-014` MongoDB 认证分类通过；`LOAD-014` Kafka 重连通过；
  `LOAD-015` 证明 Redis 重启后旧世代租约 release 返回 404、24 个实例重新注册且 readiness
  恢复；`LOAD-016` PostgreSQL 重启恢复通过。
- 终态复核：24 个本轮算子容器全部停止，`nvidia-smi` 无计算进程；原 `ocr-v6-amd` 恢复为
  snapshot 中的已停止状态；PostgreSQL、Redis、Kafka、MongoDB 与四个平台服务均健康；
  无 runner/holder 进程且 release-tag 锁可由 `flock -n` 立即获取，终端记录
  `restore: complete`。`/data/result` 未删除。
- 报告边界：8A.3 只关闭 deployment 阶段，不生成覆盖全部 243 条的 `summary/report.json`。
  93 条阶段执行记录和双终态是本阶段权威结果；完整 `overall_status=通过` 只在 `8A.7` 的
  217 条反例与 26 条压力用例总验收后生成。因此本轮完成 OpenSpec `8A.3` 和 `DEC-022`，
  `DEC-024`、PPT/ASR、视觉、在线业务泳道及 243 条最终验收继续保持未完成。

## 2026-08-19 - 8A.3 `4af04c69` 第二轮终态与 Redis 租约 epoch 修复

- 第二轮 release：Git SHA 为 `4af04c69a50048ab8995a4fd436d54b88051bb05`，不可变目录为
  `/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/v1.0_260812/4af04c69a50048ab8995a4fd436d54b88051bb05`。
  该目录保持只读。本轮 `CODEX_STAGE45_COMPLETE failures=0`：FaceRec 三卡真实人物创建/识别、
  18 个 GPU 实例推理与 CUDA PID 生命周期、24 实例注册、6 个 CPU 实例 Smoke 和 8/8 算子
  full Smoke 全部通过；最终仍为
  `CODEX_8A3_TERMINAL stage45_failures=0 deployment_status=1`，因此不能勾选 `8A.3`。
- deployment 唯一失败为 `LOAD-015`。`INF-014` 的结构化 MongoDB 认证分类、`LOAD-014` 的
  Kafka/Worker 恢复和 FaceRec `recognize` capability 均已通过。`LOAD-015` 通过生产租约 API
  建立了唯一真实租约，但 Redis 使用 AOF，容器重启后租约 hash 与实例租约 ZSET 一同恢复；
  旧租约 release 返回 200，而用例要求旧 Redis 进程签发的容量所有权失效。
- 根因不在 FaceRec 镜像、GPU、MongoDB 或服务器 Conda。容量租约此前只有 TTL，没有 Redis
  进程世代；AOF 能保留绝对过期时间，却不能证明持有者在 Redis 重启后仍拥有执行权。关闭
  Redis 持久化会同时丢失实例注册和运维生命周期，不采用。
- 修正：租约 hash 原子记录当前 Redis `run_id`。申请、续约、释放和容量统计 Lua 脚本均在
  Redis 内读取当前 `run_id`；世代不匹配或旧版本缺少该字段的租约会从 hash/ZSET 原子清除并
  按不存在处理。新租约申请也会先清除旧世代成员，避免无人主动 release 时容量永久被占用；
  实例注册、心跳和 PostgreSQL 审计事实不受影响。
- TDD：先复现“旧世代租约 release 仍成功”和“旧世代租约阻塞新容量”两个 RED，再做上述
  最小修正；真实 Redis Registry 集成测试为 `16 passed`，完整部署组合回归为
  `1276 passed, 3 skipped`。3 个 skip 仅因本机没有远端注册令牌和 Canonical FaceRec GPU
  容器；Ruff、strict Mypy、compileall、Harness 一致性、OpenSpec strict 和
  `git diff --check` 均通过。第三轮双终态为零前 `DEC-022` 继续保持部分符合。

## 2026-08-18 - 8A.3 `f79d0632` 首轮终态与三项部署缺陷修复

- 首轮 release：Git SHA 为 `f79d0632ad86b103a85ad7f46128a9d48830692a`，不可变目录为
  `/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/v1.0_260812/f79d0632ad86b103a85ad7f46128a9d48830692a`。
  该目录保持只读证据边界，不在修复后原地覆盖。该轮终态为
  `CODEX_STAGE45_COMPLETE failures=0` 和
  `CODEX_8A3_TERMINAL stage45_failures=0 deployment_status=1`，因此 `8A.3` 仍未完成。
- 已通过事实：FaceRec 三卡真实人物创建/识别通过；18 个 GPU 算子实例均完成真实推理、
  停止、CUDA PID 消失、重启和注册恢复；24 个实例注册、PPT Slice/Text Analysis 六个 CPU
  实例 Smoke 以及八算子 full Smoke 均通过。FaceRec 使用 x86 Docker 镜像与 NVIDIA
  Container Runtime 完成验证，不依赖 `192.168.29.11` 上的 Conda 环境。
- deployment 结果：93 项中 90 项通过，失败项仅为 `INF-014`、`LOAD-014` 和 `LOAD-015`。
  阶段 6 已停止本轮 24 个测试算子并释放 GPU，平台与 PostgreSQL/Redis/Kafka/MongoDB 保持健康；
  原业务容器按 snapshot 恢复，canonical paused ledger 归档为唯一终态 audit，维护锁释放。
- `INF-014` 根因：MongoDB readiness 首次认证失败后，PyMongo 外层
  `ServerSelectionTimeoutError` 只保留格式化消息，结构化
  `OperationFailure(code=18, codeName=AuthenticationFailed)` 保留在 topology server
  description 中。分类器现在显式读取 `topology_description.server_descriptions()` 的结构化错误，
  仍只接受 code 18 与 `AuthenticationFailed` 的组合；不解析异常字符串，普通网络错误继续
  fail closed。
- `LOAD-014` 根因：前一项 `LOAD-013` 重启 control-service 时，节点执行器的瞬时
  `httpx.ConnectError` 逃逸并触发共享 `stop_event`，Outbox Publisher 与 Consumer 随后退出；
  `LOAD-013` 又只等待 control readiness，直到 `LOAD-014` 才暴露。执行轮询现在只对
  `httpx.NetworkError` 和 `httpx.TimeoutException` 按既有轮询间隔重试；
  `UnsupportedProtocol` 与普通运行时错误仍 fail-stop。`LOAD-013` 同时等待 control 和
  orchestrator readiness。
- `LOAD-015` 根因：FaceRec 的 `operator_code` 是 `facerec`，但注册 capability 是
  `recognize`；原 runner 错把 `facerec` 用作租约能力，Redis 能力集合查询为空并返回 503。
  scenario、租约请求、响应校验和恢复 receipt 已统一使用 `recognize`，算子编码及 Redis
  资源范围继续保持 `facerec`。
- TDD 与本地门禁：三项缺陷均有对应 RED/GREEN；额外代码复审发现并修复过宽的
  `TransportError` 捕获，`UnsupportedProtocol` 回归在旧实现上以 timeout 失败，收窄后
  orchestrator runtime 为 `10 passed`。部署组合回归为 `1260 passed, 3 skipped`；3 个 skip
  仅因本机没有远端注册令牌和 Canonical FaceRec GPU 容器。Ruff、3 个生产文件 strict
  Mypy、compileall、Harness 一致性 `5 passed`、OpenSpec strict 和 `git diff --check` 均通过。
- 重跑门禁：修复必须进入新 Git SHA 和新不可变 release；`PREVIOUS_RELEASE_ROOT` 必须精确指向
  上述 `f79d0632...` release。只有新 release 同时得到
  `CODEX_STAGE45_COMPLETE failures=0` 与
  `CODEX_8A3_TERMINAL stage45_failures=0 deployment_status=0`，且报告通过、测试算子清理、
  原业务恢复和维护锁释放全部有证据后，才允许勾选 OpenSpec `8A.3` 和更新 `DEC-022`。

## 2026-08-19 - 8A.3 deployment runner 现场失败收敛与重跑门禁

- 失败 release：`fd079383f507a5d7d16cd20209874deeab1cfd79` 保持只读。该轮 18 个 GPU
  实例都已进入真实推理、停止、CUDA PID 消失、重启和注册恢复流程；六个 CPU 实例 Smoke
  与八算子 full Smoke 通过。`CODEX_STAGE45_COMPLETE failures=18` 的统一根因是 GPU2
  `temperature.gpu.tlimit=[N/A]` 被采集器在目标卡筛选前强制转为浮点；修复已在
  `3a0af94` 记录。deployment 批次另有 26 个失败，其中 GPU 用例大部分是上述基础 evidence
  FAIL 的级联，独立问题为 LOAD schema/生命周期/租约、INF FaceRec 探针、REG-009 装配和
  GPU registration evidence 合同。
- LOAD 修正：课程事实查询从不存在的 `task_nodes.operator_code` 改为正式字段
  `required_capability`；SIGTERM 后允许注册客户端按合同注销实例，但容器必须同时满足
  `Running=false`、`ExitCode=0`、`OOMKilled=false` 和空 Docker state error，不能把超时
  SIGKILL 当成优雅退出；LOAD-015 只对容量 503 做 30 秒有界重试，成功后先持久化精确租约
  receipt 再重启 Redis，持续失败保留白名单容量快照。疑似 token、密码、Authorization 或
  URI 凭据的详情整段脱敏，不把任意服务返回值写入普通报告。
- INF/REG 修正：FaceRec host readiness 与容器探针共用唯一 marker-frame strict JSON 解码；
  Mongo 认证探针只接受直接或 `ServerSelectionTimeoutError` 包裹的 code 18
  `AuthenticationFailed`，普通网络超时继续 fail closed；REG-009 重新获得按 run/case 隔离的
  Redis registry，不再把 `None` 注入生产 `AuditedOperatorRegistry`。
- GPU 修正：canonical running/stopped evidence 必须先同时为 PASS，结构错误始终输出单一
  strict JSON；GPU-012/013 不再因 FAIL/FAIL baseline 错误通过；registration producer 的
  成功 envelope 增加白名单化 `validated_instances`，只保留契约字段和 `gpu` 标签，GPU-018
  从唯一真实实例记录校验物理卡标签并拒绝旧扁平假 fixture。
- 证据分级：INF/REG 目录项的 `safety` 均为 `isolated_mutation`；INF 的 mode 是
  `controlled_input`，REG 保留 `canonical_runtime` mode 以表示使用生产注册组件和真实隔离 Redis；
  GPU-012/013/018 使用本 release 的真实 canonical PASS evidence，在内存深拷贝上注入反例以
  证明验证器会 fail closed，不会真的制造远端 OOM、错误注册或停止 MongoDB。真实 GPU 推理、
  无 OOM、实例注册与生命周期由 stage45/preflight/Smoke 证据负责；实际并发升压和 OOM 上限
  属 8A.7。Harness 与最终报告不得把受控反例描述成真实故障注入。
- TDD 与本地门禁：各缺陷均先复现 RED 后最小修正。聚焦全量为 GPU evidence `108 passed`、
  foundation `510 passed, 3 skipped`、LOAD `91 passed`；合并部署相关回归为
  `1248 passed, 3 skipped`，安全收口后核心三套为 `709 passed, 3 skipped`。3 个 skip 只因
  本机没有远端注册令牌/Canonical FaceRec 容器。Ruff、5 个生产文件 strict Mypy、py_compile、
  OpenSpec strict 和 `git diff --check` 通过。
- 完成边界：本条只允许生成新的 Git SHA 并正式重跑。只有新不可变 release 同时出现
  `CODEX_STAGE45_COMPLETE failures=0` 和
  `CODEX_8A3_TERMINAL stage45_failures=0 deployment_status=0`，且原业务恢复、维护锁释放，
  才允许勾选 OpenSpec `8A.3`。

## 2026-08-19 - 8A.3 GPU 温度上限不可用值语义修正

- 现场现象：`fd079383f507a5d7d16cd20209874deeab1cfd79` 已完成八算子与四平台镜像构建、
  四平台健康、三组 GPU profile 和 CPU profile 注册；18 个 GPU 实例进入真实请求、停止、重启与
  注册恢复流程后，running 证据均在 `nvidia-smi GPU telemetry 数值字段格式异常` 处失败。
  FaceRec 与 ASR、OCR、VBas 的失败点相同，因此不是 FaceRec x86 镜像、模型、Python 或宿主
  Conda 环境问题。
- 根因证据：服务器原始查询中 GPU2 的 `temperature.gpu.tlimit` 返回 `[N/A]`，GPU0/GPU1 返回
  数值。验证器在筛选目标 GPU 前先把三张卡全部字段执行 `float()`，导致验证 GPU0/GPU1 时也被
  GPU2 的缺失值误伤；验证 GPU2 时又无法忠实表达厂商未提供的温度上限。
- 修正：先按物理索引和 UUID 唯一选择目标卡，再解析目标 telemetry；仅将 NVIDIA 明确不可用的
  `temperature.gpu.tlimit` 保存为 JSON `null`。实际温度、功耗/功耗上限、全局 GPU 利用率与
  hardware slowdown 继续强制取真实值。GPU case runner 对 `null` 温限跳过无法成立的温限比较，
  仍校验同步样本摘要、功耗边界和 slowdown 状态。
- TDD 证据：新增“非目标 GPU 的 `[N/A]` 不影响目标卡”和“目标卡温限缺失保留为 `null`”两条
  采集器回归，以及 canonical hardware validator 的缺失温限回归；三条测试均先在旧实现按现场
  原因失败，最小修正后转绿。GPU 采集器完整测试 `85 passed`，相关 GPU/stage45/维护锁测试
  `54 passed`；最终提交前仍需执行完整静态和回归门禁。
- 完成边界：`fd07938` 是失败 release，保持只读。修正必须进入新 Git SHA/新不可变 release，
  并同时出现 `CODEX_STAGE45_COMPLETE failures=0` 与
  `CODEX_8A3_TERMINAL stage45_failures=0 deployment_status=0`，才允许勾选 `8A.3`。

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
- 根因 3：`b8431c0` 已证明模块入口修复生效，deployment batch 进入后继续被
  `delegated maintenance lock holder or binding is invalid` 拒绝。Canonical Bash 使用
  `coproc { python ...; }`，`$..._PID` 是 coprocess 包装 shell，而不是真正持有锁 inode
  的 `operator_lifecycle.py`。持锁命令改为 `coproc { exec python ...; }`，使委托 PID、
  命令身份、打开 inode 和 `flock` 权威重新指向同一进程；安全校验本身没有放宽。
- 根因 3 TDD：新回归先在旧 canonical 脚本上因缺少 `exec` 失败；最小修正后，
  canonical 持锁、委托锁、parent recovery 和 8A.3 控制器聚焦回归全部通过。
- 证据与边界：前两项修正分别在 `6dedca2` 和 `b8431c0` 提交，本地完整回归、Ruff、
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

## 2026-08-20 - 统一容量与在线 OCR 最终验收门禁补强

- 先前状态：12.9 配置探针不能同 SHA 续跑；clean-clone 会把基础设施 skip 写成通过；业务 Campaign 用单个阶段结果批量宣告 150 条用例；镜像清理只依赖注册和 Smoke。
- 目标状态：最终 release 的每项结论都有实际执行统计，243 条逐案通过和维护锁成为删旧镜像的硬前置条件。
- 变更文件：配置权威验证器、clean-clone gate、业务 Campaign/runner、8A.7 总控、镜像清理门禁、对应测试和 Harness/OpenSpec 说明。
- 契约影响：A 面和算子业务接口不变；只收紧部署验收、证据复用和镜像清理条件。
- 后续复审：业务 Campaign 不再按 `JOB/FILE/PPT/...` 前缀共用一组回归结论，而是为 150 个 case 显式绑定必须出现于 JUnit 的测试语义；任一绑定缺失即失败关闭。Canonical 在中途失败时，阶段 3 之前恢复已暂停的原业务，阶段 3 之后先对账并停止本轮精确算子账本，再恢复原业务。
- 最终复审：算子生命周期 `EXIT` trap 只在账本与容器身份依赖函数全部定义后安装；更早失败由外层 trap 恢复已暂停业务。`LOAD-007` 与已批准设计统一为“允许确定性偏向，实例满容量后再选下一实例”，不再要求三实例轮询。配置权威探针入口为可执行 Bash 包装器，固定使用 clean-clone 准备的平台 `.venv/bin/python`，不依赖宿主机默认 Python 版本。
- 验证：配置/clean-clone/清理门禁 23 passed；业务逐案与 8A.7 总控原回归 27 passed；新增媒体失败关闭、显式映射和总控定向 30 passed；本轮 trap/路由定向 17 passed；四阶段映射实际 JUnit 为 99/57/36/138，全部零失败、零错误、零跳过且无缺失映射；Ruff 通过。平台全量首轮为 2652 passed/1 failed/3 skipped，唯一失败是独立阶段脚本在 `set -u` 下读取未初始化恢复标志，修复后全量复跑终态为 2653 passed/3 skipped；3 个跳过均要求本机未运行的 canonical FaceRec 容器，远端最终门禁不得跳过。
- 证据等级与结论：本地静态/单元门禁实现完成；最终 SHA 的 16 进程 release 证据、真实基础设施零 skip、业务泳道、容量/恢复和远端镜像清理仍待执行。
- 剩余风险：8 个 B 级质量项需要基于当前 release 真实产物生成独立复核证据；未取得该文件时 8A.7 按设计失败关闭。

## 2026-08-20 8A.7 首次最终 SHA 入口失败与修复

- 失败 release：`b0f5ae68cae4d50349d85b43f851bb4eb47e3424`。
- 失败位置：模型清单验证和 release 目录准备完成后，阶段 1 调用 `release-image-cleanup snapshot` 时失败；未进入镜像构建、容器替换或业务泳道。
- 原因：旧 `release-image-cleanup` 包装器用宿主机 `python3` 按文件路径启动，新增的 `scripts.milestone_2b_case_runners.safety` 包导入无法从脚本目录解析，报 `ModuleNotFoundError: scripts`。
- 修复：包装器改为可执行 Bash，固定调用平台 `.venv/bin/python -m deploy.scripts.release_image_cleanup`；新增 clean-clone 直接执行/`--help` 回归。
- 验证：镜像清理与 8A.7 定向 `15 passed`，Ruff、strict Mypy、`git diff --check` 和 OpenSpec strict 全部通过。
- 结论：该 release 只是已记录的失败证据，不得计为 12.9 或 14.x 完成；修复后必须使用新 Git SHA 新建不可变 release。

## 2026-08-20 8A.7 clean-clone 全量测试失败与修复

- 失败 release：`448f6f3f21e748fc6f9ce5b05dbcdabae82b96b3`。
- 失败位置：阶段 1 clean-clone 的平台全量 `pytest -q`；结果为 `2647 passed, 5 failed, 6 skipped`，未进入镜像构建、容器替换或业务泳道。
- 原因 1：`run_milestone_2b_case_batch.py` 被测试和 Canonical 按文件路径直接执行时，`sys.path` 只有脚本目录，三个密封 release 反例在导入 `scripts.aggregate_milestone_2b_cases` 时失败。
- 原因 2：两个早期失败测试从 Canonical 继承绝对 `PREVIOUS_RELEASE_ROOT`，临时项目中的场景脚本在目标失败命令之前被同 release tag 路径校验正确拒绝；该变量不是测试目标，夹具没有隔离外部运行环境。
- 修复：批次 runner 在导入平台包前显式加入自身项目根；早期失败测试显式设置空前驱路径。新增/保留直接文件入口回归，继续要求密封 release 返回参数错误且证据树字节级不变。
- 验证：聚焦失败用例 `5 passed`；平台全量 `2655 passed, 3 skipped`；四个根服务分别 `21/53/16/20 passed`；Ruff、strict Mypy、compileall、无 `PYTHONPATH` 文件入口、OpenSpec strict 和 `git diff --check` 均通过。3 个本机 skip 只因未提供 canonical FaceRec 注册令牌，远端不得跳过。
- 恢复复核：Canonical 输出 `restore: complete`；维护锁已释放，原 `ocr-v6-amd` 保持执行前的 Exited 状态，PostgreSQL、Redis、Kafka、MongoDB 和四个平台容器均为 healthy。
- 结论：该 release 不得计入 OpenSpec 12.9/14.x；修复验证通过后必须以新 Git SHA、新不可变 release 重跑完整 8A.7。

## 2026-08-20 8A.7 clean-clone JUnit 汇总失败与修复

- 失败 release：`7df1c212dc219c1422b5ba857cbd426b1f3e1da5`。
- 失败位置：阶段 1 clean-clone 已通过平台全量与四服务回归，在解析真实 PostgreSQL/Redis JUnit 时报告零用例；未进入镜像构建、容器替换或业务泳道。
- 原因：pytest 的 JUnit 根节点为不带统计属性的 `<testsuites>`，实际统计位于直接子 `<testsuite>`；旧解析器允许该根节点，却仍只从根属性读取并把缺失字段默认为零。
- 修复：根为 `testsuite` 时严格读取自身；根为 `testsuites` 时优先读取完整根汇总，否则严格汇总直接子 suite。缺失、部分汇总、非整数、负数、零用例、失败、错误和跳过继续失败关闭。
- 验证：JUnit 聚焦测试 `10 passed`；真实 pytest XML 解析为 `tests=10/failures=0/errors=0/skipped=0`；平台全量 `2658 passed, 3 skipped`；Ruff、strict Mypy、compileall、OpenSpec strict 和 `git diff --check` 均通过。3 个本机 skip 仍只因未提供 canonical FaceRec 注册令牌，远端不得跳过。
- 恢复复核：Canonical 输出 `restore: complete`；维护锁已释放，原 `ocr-v6-amd` 保持执行前的 Exited 状态，PostgreSQL、Redis、Kafka、MongoDB 和四个平台容器均为 healthy。
- 结论：该 release 不得计入 OpenSpec 12.9/14.x；修复验证通过后必须以新 Git SHA、新不可变 release 重跑完整 8A.7。

## 2026-08-20 8A.7 completed direct 算子账本回溯失败与修复

- 失败 release：`7b7d135cc042b81da45000df4297d4f993723d54`。
- 已通过门禁：clean-clone、真实 PostgreSQL/Redis/Kafka JUnit、16 进程配置权威、八类算子镜像构建与最终 SHA 检查。
- 失败位置：启动任何新算子容器前解析 baseline/new 祖先，返回 `no complete operator ledger ancestor`；平台/算子容器未替换，旧镜像未清理。
- 现场事实：前驱链 `7df1c21 -> 448f6f3 -> b6706fc` 的 marker 均为当前 UID、单链接、`0400`；前两个 release 是已通过唯一 `0400` 终态 audit 的 completed direct maintenance，`b6706fc` 具有完整空 baseline 和 24 项 new ledger。
- 原因：resolver 只允许活动 `direct` 状态沿 predecessor marker，未允许已经通过同等严格终态验证的 `completed` direct 状态继续只读回溯。
- 修复：合法的 `direct` 与 `completed` direct 都可在存在严格 marker 时继续回溯；completed 候选仍先校验 snapshot、终态 audit 与当前恢复事实，缺 marker、partial、环、无账本祖先和 `current - baseline != new` 继续失败关闭。新增 completed 成功链、历史文件不变与缺 marker 反例。
- 验证：resolver 聚焦测试 `10 passed`；完整阶段 3/task9 合同 `248 passed`；平台全量 `2660 passed, 3 skipped`；Ruff、strict Mypy、compileall、OpenSpec strict 和 `git diff --check` 均通过。3 个本机 skip 仍只因未提供 canonical FaceRec 注册令牌，远端不得跳过。
- 恢复复核：Canonical 输出 `restore: complete`；维护锁已释放，原 `ocr-v6-amd` 保持 Exited，PostgreSQL、Redis、Kafka、MongoDB 和四个平台容器均为 healthy。
- 结论：该 release 不得计入 OpenSpec 14.x 完成；修复验证通过后必须以新 Git SHA、新不可变 release 重跑完整 8A.7。

## 2026-08-20 8A.7 平台迁移遗漏与失败恢复临时文件缺陷

- 失败 release：`76aa93a37a5e801aadcdd46a47e6e1bb76bf8f8c`。
- 已通过门禁：clean-clone 7 组命令、真实 PostgreSQL/Redis/Kafka JUnit、最终 SHA 的 16 进程配置权威证据，以及八类算子镜像构建/revision 校验。
- 失败位置：四个平台镜像完成构建后，`control-service` readiness 持续返回 HTTP 503，Compose 健康等待失败，尚未启动 24 个新算子实例或进入业务 Campaign。
- 原因 1：持久 PostgreSQL 已执行到 `0005`，但 Canonical 在替换平台容器前没有应用当前 `0006_course_task_type_submission.sql`；readiness 明确报告缺少 `course_task_types.submission_id`。
- 原因 2：异常退出时 `cleanup_operator_lifecycle` 在验证 new ledger 容器身份之前先删除了临时 Compose service allowlist，导致精确恢复核验报“无法读取权威算子 service allowlist”，未自动输出 `restore: complete`。
- 现场恢复：先核验 new ledger 精确包含 24 个 `algorithm-operators` Compose 容器、service 均属于权威 24 项清单且状态均为 Exited，再调用 canonical `restore-existing-containers`；终态输出 `restore: complete`，生成唯一只读 audit，维护锁释放，原 `ocr-v6-amd` 保持 Exited。
- 修复：新增幂等 `apply-course-task-submission-migration`，在平台替换前应用/核验 `0006`，未知前置 schema 失败关闭；把临时 allowlist 清理移动到 new ledger 核验、精确停止和原业务恢复之后。同步部署文档、OpenSpec 设计与回归用例。
- 聚焦验证：迁移首次执行/重复执行/异常 schema、平台启动顺序、partial-up 精确停止与完整 cleanup 共 `5 passed`，脚本 `bash -n` 通过。
- 结论：该 release 仍不得计入 OpenSpec 12.9/14.x 完成证据；修复必须以新 Git SHA 和本失败 release 作为立即前驱重跑完整 8A.7。

## 2026-08-20 8A.7 PostgreSQL 预检目录未同步 0006

- 失败 release：`0d8ee4af910b739e3bbca90c8088986e3920bc7a`。
- 已通过门禁：clean-clone 7 组命令、最终 SHA 的 16 进程配置权威、八类算子镜像构建/revision，以及四个平台镜像构建和健康检查。
- 迁移证据：Canonical 在平台替换前输出 `control-schema-migration: already applied`，四个平台服务均为 healthy，证明 `0006` 应用与幂等路径有效。
- 失败位置：runtime preflight 对 PostgreSQL 实际列目录执行严格对账时报告 `PostgreSQL column catalog does not match expected migration fields`，尚未启动本轮 24 个算子或进入业务 Campaign。
- 原因：Control Service readiness 已把 `course_task_types.submission_id` 作为必需列，但独立部署 preflight 的 `EXPECTED_DATABASE_COLUMNS` 及其测试夹具仍停在 `0005`。
- 恢复：Canonical 对继承的 24 项 new ledger 完成身份核验和精确停止，输出 `restore: complete`，维护锁释放，原 `ocr-v6-amd` 保持 Exited。
- 修复：把 `submission_id` 纳入 runtime preflight 权威列目录和测试夹具；新增跨边界断言，要求部署 preflight 的列集合始终与 Control Service readiness 的 `CONTROL_SCHEMA_COLUMNS` 完全一致，避免后续迁移再次双写漂移。
- 结论：该 release 不得计入 OpenSpec 12.9/14.x 完成证据；修复验证通过后必须以新 Git SHA 和本失败 release 作为立即前驱重跑完整 8A.7。

## 2026-08-20 8A.7 业务 Campaign 参数拼接预检失败

- 失败 release：`97b9b079325505d8858cfd8dc5649d0a2f2f342d`。
- 已通过门禁：模型资产、不可变 release 目录、宿主机预检、现有容器快照和基础设施健康；clean-clone 全量测试正在运行时终止，未构建新镜像、替换平台/算子容器或进入业务 Campaign。
- 发现位置：复核总控展开命令时，四个 `run-milestone-2b-business-campaign` 命令的每个参数前均存在字面量 `+`；本地最小复现确认该字符会成为非法命令行参数。
- 原因：`_campaign_command` 的续行连接符误包含补丁标记字符，既有测试只检查阶段文本存在和顺序，没有真实执行生成命令并核对 argv。
- 恢复：主动终止耗时但必然失败的运行；由于 SSH 中断早于外层 trap 归档，随后使用本 release 的精确 snapshot/paused ledger 执行 Canonical restore，输出 `restore: complete`，24 个算子和原 `ocr-v6-amd` 均保持 Exited。
- 修复：删除续行中的字面量 `+`；新增 shell 真实执行回归，以捕获器核对离线 Campaign 收到的完整 argv，禁止任何未声明参数混入。
- 验证：argv 聚焦回归 `5 passed`；8A.7 总控、部署脚本和 Task 9 完整回归 `558 passed`；Ruff、strict Mypy、OpenSpec strict 和 `git diff --check` 通过。
- 结论：该 release 不得计入 OpenSpec 12.9/14.x；修复验证通过后必须以新 Git SHA 和本失败 release 作为立即前驱重跑完整 8A.7。

## 2026-08-20 8A.7 视觉抽帧 cgroup OOM 与运行时就绪缺口

- 失败 release：`ecadb0cb1e884f24c18aa77965d5695101931d2f`。
- 已通过门禁：clean-clone 七组验证、最终 SHA 的 16 进程配置权威、八类镜像 revision、`0006` 幂等迁移、四平台健康、24 实例注册、18 个 GPU 实例真实推理与进程归属、6 个 CPU 实例 Smoke、8 类算子 full Smoke、deployment 反例和 26 条压力用例。
- 失败位置：真实 full-course offline Campaign。ASR 转写与课程脑图已完成；PPT Slice 正在执行；教师/学生视觉节点开始粗粒度扫描后不再推进。
- 根因证据：Vision Orchestrator `/ready` 返回 `visual_command_consumer` 已因 ffmpeg `SIGKILL` 退出；宿主机 dmesg 明确记录容器 cgroup OOM。`FFmpegFrameExtractor.extract()` 对全部粗扫时间点使用无界 `asyncio.gather(asyncio.to_thread(...))`，单条长视频同时创建大量 ffmpeg；宿主机尚有约 `92 GiB` available，但该容器 `HostConfig.Memory=4 GiB`，证明是容器内并发峰值而非宿主机容量不足。Compose 当时只探测 `/health`，后台循环退出后仍显示 healthy。
- 恢复：确认 Campaign 不可能进入终态后终止运行。SSH 退出没有触发远端 `EXIT` trap，随后使用本 release 已发布的排序 baseline/new 账本、完整容器 ID、`algorithm-operators` project 和权威 24 项 service allowlist 逐项核验，精确停止 24 个 new ledger 容器，再调用既有 `restore-existing-containers`；终态输出 `restore: complete` 与 `MANUAL_CANONICAL_RECOVERY status=complete stopped=24`，`new_running=0`，原 `ocr-v6-amd` 按基线保持 Exited。
- 修复边界：新增 `media.max_concurrent_processes=2`，时长探测及 T/S 抽帧共享同一信号量；不改变扫描点、VBas 批次或平台注册容量。Vision Compose 健康检查改用 `/ready`。新增跨 T/S 并发单元测试、非法配置测试和 Compose 就绪探针合同。
- 本地验证：Vision Orchestrator 全项目 `23 passed`，平台 Compose 合同 `18 passed`，Harness 一致性 `5 passed`；Compose 展开、Ruff、strict Mypy、compileall、`app.main` 导入、OpenSpec strict 和 `git diff --check` 全部通过。本地结果只证明并发/配置/探针合同，不能替代下一 release 的真实长视频和 cgroup 证据。
- 结论：该 release 可作为失败诊断证据，但不得计入 OpenSpec 14.3-14.7；修复验证通过后必须用新 SHA、以本 release 为立即前驱重跑完整 8A.7。

## 2026-08-20 8A.7 视觉容量等待被误判为后台循环故障

- 失败 release：`c07df67910558716985941bb2feff73b637bd844`；立即前驱为 `ecadb0cb1e884f24c18aa77965d5695101931d2f`。
- 已通过门禁：clean-clone 七组验证、最终 SHA 的 16 进程配置权威、八类算子镜像、四个平台镜像和 PostgreSQL `0006` 迁移路径。
- 失败位置：Kafka 保留上轮未完成的视觉命令；Canonical 固定先启动平台、后启动 VBas。Vision Consumer 在 VBas 尚未注册时申请租约收到 HTTP `503`，将它当作致命异常退出；`/ready` 正确变为 `503`，Compose 因此将 Vision 判定为 unhealthy。
- 根因：已确认的“离线容量不足应等待”规则只在业务节点层表达，`CapacityLeaseHttpClient` 没有区分容量 `503` 和其他 HTTP/协议错误，Consumer 也没有保持当前 offset 的容量重试路径。
- 恢复：Canonical 最终输出 `restore: complete`；release 内存在唯一 `0400` 终态 audit。baseline 为 `0`，new ledger 为 `24`，24 个 `algorithm-operators` 容器均未运行，原 `ocr-v6-amd` 保持 Exited；维护锁已用非阻塞 `flock` 复核为可获取。
- 修复边界：仅将租约申请中明确的“暂无可用算子容量” HTTP `503` 映射为可恢复等待；Consumer 按 `worker.poll_interval_seconds` 原地重试且不提交 offset，关闭信号可终止等待。注册中心不可用等其他 `503`、HTTP `400/401`、响应协议错误等仍是致命错误，避免 `/ready` 掩盖配置或依赖故障。
- 本地验证：Vision Orchestrator 完整套件 `32 passed`，Ruff 和 strict Mypy 通过；最终数量以本修复提交前重跑记录为准。
- 结论：该 release 只能作为启动顺序缺口证据，不得计入 OpenSpec 12.9/14.x；必须用包含修复的新 SHA、以本 release 为立即前驱重跑完整 8A.7。

## 2026-08-20 8A.7 真实课程候选窗口上限与中断恢复缺口

- 失败 release：`bec262b46bd7f570e43dc1a74b5f7e336f935084`；立即前驱为 `c07df67910558716985941bb2feff73b637bd844`。
- 已通过门禁：clean-clone、最终 SHA 的 16 进程配置权威、四平台/八类算子镜像 revision、PostgreSQL `0006`、24 实例注册与首心跳、18 个 GPU 实例逐实例真实推理、6 个 CPU 实例 Smoke、八算子综合 Smoke，以及 PPT→OCR→关键词和 ASR→课程脑图真实泳道。
- 失败位置：Kafka 中的教师视觉命令粗扫产生 `31` 个候选窗口，超过 `scan.max_candidate_windows=20`；`/ready` 明确返回 `视觉候选窗口超过上限: 31`。该故障不是 VBas 容量、GPU OOM、FaceRec 或 Kafka 连通性问题。
- 设计修复：保留可配置有界保护，将默认候选窗口上限调整为 `128`，并继续使用 `max_detection_points=10000` 作为第二道保护；增加 31 个窗口通过和 129 个窗口失败关闭回归。
- 中断缺口：主动终止 Canonical 后，Python 总控没有等待远端 Bash `EXIT` trap 完成，24 个本轮算子容器仍在运行。修复后总控将 Bash 放入独立 session；收到 `SIGHUP/SIGINT/SIGTERM` 时保留 `operator_lifecycle.py hold-lock` 及其后代，只终止其他运行子进程和外层 Bash，并持续等待 trap 恢复结束。
- 现场恢复：在同 release-tag 非阻塞维护锁下，验证 `baseline=0`、`new=24`、账本按字节序唯一、全部 ID 为 64 位且 Docker inspect/`algorithm-operators`/24 项 service allowlist 一致后，只停止该 24 个容器并执行权威 restore。终态为 `operator_running=0`、原 `ocr-v6-amd=Exited(143)`、四平台与四基础设施继续运行、维护锁已释放；生成唯一 `0400` 恢复 audit，未执行 prune 或镜像删除。
- 本地验证：候选窗口/中断恢复定向 `12 passed`，Vision Orchestrator 全套 `32 passed`，平台全量 `2667 passed, 3 skipped`；3 个 skip 仅为需要远端 canonical FaceRec 注册 Token/容器的本机条件，远端不得跳过。Vision 严格 Mypy、Ruff、OpenSpec strict 和 `git diff --check` 均通过。
- 结论：该 release 的已通过证据可作为故障诊断，但不得计入 OpenSpec 12.9/14.1/14.3-14.7 的最终 SHA 证据；必须以本 release 为立即前驱重跑完整 8A.7。

## 2026-08-20 8A.7 B 级复核时序与早期中断恢复缺口

- 失败 release：`3880772431313e45406e56601f5bbaabe951b039`；立即前驱为 `bec262b46bd7f570e43dc1a74b5f7e336f935084`。
- 主动中断原因：fresh Campaign 在四条课程任务完成前就要求外部 `--manual-review-json`，但 7 个离线质量项必须使用本 SHA 的 PPT/ASR/OCR/关键词结果，`VIS-025` 必须再等待视觉结果；旧 SHA 只有 7 项且不得复用，因此原流程会确定性失败。
- 中断现场：尚未建立算子 baseline/new 账本，也没有创建本轮算子容器；`operator_running=0`，原 `ocr-v6-amd` 保持 `Exited(143)`，四个平台与 PostgreSQL、Redis、Kafka、MongoDB 保持运行。
- 恢复：使用本 release 唯一 snapshot 与空 paused ledger 执行 `restore-existing-containers`，输出 `restore: complete`，生成唯一 `0400` audit `existing-containers.jsonl.paused.jsonl.audit.fc0d303c76cd4a8d97e0cf0614fc0af8.jsonl`；未执行镜像清理、prune、卷或结果目录删除。
- 暴露的第二缺口：Python 总控收到 `SIGINT` 后退出，但没有等待本轮 Bash 自动生成 restore audit。修复采用显式 Bash `HUP/INT/TERM -> exit` trap，让既有 `EXIT` 恢复路径先完成；本地中断回归只能证明控制逻辑，下一 SHA 仍须真实中断门禁复核。
- B 级复核修复：offline/vision 真实结果完成后分别发布 write-once 请求并有界等待；新增受控发布器，逐项核验当前 `case_id/git_sha/task_id/status`，索引和证据要求当前 UID、`0600`、单硬链接及无符号链接祖先。课程图片、联系表和识别全文留在 Git 外受限目录，普通 release 只保留摘要、散列与不透明证据编号。
- 本地验证：复核/中断/Harness 定向 `72 passed`；平台全量 `2673 passed, 3 skipped`，3 个 skip 仅因本机没有 canonical FaceRec 注册 Token/容器，远端不得跳过；Ruff、strict Mypy、compileall/import、OpenSpec strict 和 `git diff --check` 均通过。
- 结论：该 release 仅作为时序和恢复缺口证据，不得计入 12.9/14.1/14.3-14.7；修复提交必须以新完整 SHA 重跑 8A.7。

## 2026-08-20 - 8A.7 deployment 变异后离线运行时稳定门禁

- 先前状态：`580263d8e516675fa931151138ac6e3bb1483396` 已通过 clean-clone、配置权威、镜像、24 实例、GPU/PPT/Text Analysis/Comprehensive Smoke 和 deployment 93/93，但进入真实离线任务后两个后台服务 readiness 均为 503。
- 根因：`LOAD-014` 只验证 Orchestrator；Vision 将 VBas 本地保护的 HTTP 429 当成致命错误退出 Consumer。若只改为整命令重试，又会因 Vision 并发 2 与 VBas 本地并发 1 的差异形成活锁。
- 目标状态：`429` 在单批次内可中断重试，成功兄弟不重做；致命异常取消并收割兄弟。Kafka 重启同时验证两个 Consumer，deployment 后再执行精确离线服务稳定门禁。
- 契约影响：A 面、VBas HTTP 路由/字段/响应、Kafka topic 和 offset 契约不变；只收紧过载恢复、并发收割和验收门禁。
- 恢复事实：受控 `SIGINT` 后 Canonical 输出 `restore: complete`；24 个本轮算子已停止，唯一恢复 audit 为当前 UID、单链接、`0400`，未执行镜像删除或越界清理。
- 本地验证：Vision Orchestrator `33 passed`；VBas/运行时/部署定向 `135 passed`；平台全量 `2681 passed, 3 skipped`，3 个 skip 仅因本机缺 canonical FaceRec Token/容器，远端不得跳过。Ruff、Mypy 139 个源码文件、OpenSpec strict、Bash 语法和 `git diff --check` 通过。
- 验证边界：修复需提交新 SHA，然后以 `580263d8...` 为立即前驱重跑远端完整 Canonical；旧 release 不得补写通过。

## 2026-08-20 - 8A.7 长 ASR 推理心跳间隙导致在途租约误失效

- 失败 release：`702dba67613e3b7c0f14fb5f67c7d24ce1b4c2da`；立即前驱为 `580263d8e516675fa931151138ac6e3bb1483396`。
- 已通过门禁：clean-clone 七组/六层验证、最终 SHA 的 16 进程配置权威、四平台与八类算子镜像、PostgreSQL `0006`、24 实例注册/首心跳、18 个 GPU 逐实例真实推理、6 个 CPU Smoke、八算子综合 Smoke、deployment 用例与事后 runtime preflight。
- 失败位置：真实 full-course offline Campaign。教师和学生视觉任务已完成，PPT Slice 正常执行；ASR 在长音频推理期间失败为“算子容量租约续租失败”。
- 根因证据：`asr-offline-gpu0` 从 `13:48:14.505Z` 到 `13:48:46.653Z` 出现约 32 秒心跳间隙，但推理继续并于 `13:48:46.115Z` 产出 1293 段；Orchestrator 在 `13:48:43.084Z` 按时续租得到 HTTP 404。Redis 续租 Lua 将短暂缺失的 heartbeat key 当成租约终止条件，误删了仍在执行且未过期的租约。
- 修复边界：新租约申请仍要求有效心跳、`ONLINE` 和模型就绪；既有未过期租约续期不再仅因 heartbeat key 短暂缺失而失败。心跳缺失期间实例仍拒绝新租约，旧租约继续占用容量；实例注销、同 ID 重注册、显式 `OFFLINE`、Redis 运行标识变化和租约自身到期仍保持失败关闭。
- 本地验证：真实 Redis 注册表 `23 passed`，Control/租约跨服务定向 `59 passed`。新增用例明确覆盖“心跳过期后旧租约可续期、新租约被拒绝、心跳恢复后旧租约仍占满容量”。
- 失败现场恢复：向 Canonical Controller 发送 `SIGINT`，保留 release-tag 锁直到精确停止本轮 24 个算子容器完成。原 `ocr-v6-amd` 基线状态为 `exited + unless-stopped`，恢复后身份和状态一致；空暂停账本已归档为唯一 `0400` 终态审计 `existing-containers.jsonl.paused.jsonl.audit.4e076eac00d844b0818b80e4fba3ecc2.jsonl`，release-tag 锁可非阻塞重新获取；未执行 prune、`down -v`、卷/数据/证据删除。
- 重跑参数校正：修复提交 `c5ba9b10b876def1d20ff05e982a01a1218d2db8` 首次启动后，在进入业务 Campaign 前发现 P 视频 URL 的百分号编码少了课程目录片段，立即向 Controller 发送 `SIGINT`。该 release 未发布算子账本或业务结果，空暂停账本已归档为唯一 `0400` 终态审计 `existing-containers.jsonl.paused.jsonl.audit.b25b628ab66f4501819222e1e7524d5f.jsonl`。由于已恢复的 SHA 不得重用，后续必须以新 SHA 和 `c5ba9b10...` 立即前驱重跑，并使用与 `702dba67...` 已验证课程完全相同的 T/S/P URL。
- 新 SHA 配置权威证据：`aae96b046dea1d724f8656c07ee7b5e89ac14d73` 的 `preflight/operator-config-authority.json` 以 8 算子 × 本地安全/受控部署两组配置启动 16 个独立子进程，全部返回 `PASS`。证据中 Git SHA、注册开关、Control URL、心跳、确认容量和六 GPU/两 CPU 要求全部一致；五个旧环境变量已注入但无法覆盖 TOML。文件为当前 UID 所有、`0600`、单硬链接，复读 SHA-256 为 `b1ee8db7741923b3272e23b5d9e700c4a5e3c5d2432b235b51222008f59f100a`，OpenSpec 12.9 已完成。
- 验证边界：本地测试不能替代真实长 ASR；当前失败 release 不得计入 OpenSpec 14.3-14.7，必须用包含修复的新 SHA 重跑完整 Canonical。

## 2026-08-21 - 8A.7 迟到视觉进度事件破坏 Consumer 幂等性

- 失败 release：`aae96b046dea1d724f8656c07ee7b5e89ac14d73`；直接前驱为 `c5ba9b10b876def1d20ff05e982a01a1218d2db8`。
- 已通过门禁：clean-clone 静态/单元/真实 PostgreSQL、Redis、Kafka，16 进程配置权威，四平台与八类算子镜像 revision，24/24 实例注册，18/18 GPU 实例真实推理，6/6 CPU 实例 Smoke，8/8 算子综合 Smoke，以及 deployment `76` 条反例与 `17` 条压力用例，共 `93/93` 通过。OpenSpec 14.1 的六层证据已由当前 release 和正式聚合器复核通过。真实离线课程中 ASR 转写与教师行为节点已完成，学生视觉粗扫和 PPT Slice 已开始；Vision Orchestrator 持续就绪，VBas 本地 `429` 已表现为“释放当前租约、仅重试失败批次、保留成功批次”。
- 失败位置：Orchestrator `/ops/readiness` 返回 `503`，`visual_event_consumer` 错误为 `只有处理中节点可以更新进度: 165`，随后其余 Orchestrator 后台循环全部停止。数据库中的节点 `165` 已为完成态 `60`，因此本轮不是 deployment 后的无因外部中断。
- 根因：Vision Orchestrator 先持久化终态，再发布完成事件；已经发布但尚未消费的进度事件可能在节点完成后到达。Orchestrator 虽然在更新前检查节点状态，但检查和 `update_node_progress` 不在同一事务内，节点可在两步之间从 `RUNNING` 变为 `COMPLETED`。Repository 的终态保护因此抛出 `ValueError`，迟到消息被误判为后台循环致命故障且 offset 未提交。
- 修复边界：进度更新遇到 Repository 状态校验错误时重新读取节点；只有节点已进入 `COMPLETED` 才把该进度事件作为幂等成功，其他状态和其他持久化异常继续失败关闭。已完成任务类型的重复终态事件直接幂等确认，不重复写结果或汇总；A 面、Kafka 消息结构、视觉结果持久化和节点状态机不变。
- 本地验证：在平台 `.venv` 中从 `orchestrator_service/` 执行完整测试，结果 `56 passed`；新增用例覆盖“RUNNING 初读后并发完成”“仍为 RUNNING 时错误不得吞掉”“迟到进度/重复终态提交 offset 并继续消费”和“身份不一致不提交”。状态冲突使用 `RepositoryStateConflictError` 显式分类，不吞掉其他 `ValueError`。变更文件 Ruff、strict Mypy、`compileall`、OpenSpec strict 与 `git diff --check` 通过。
- 安全恢复：Canonical `EXIT` 路径根据排序的 `baseline/new` 账本和权威 Compose allowlist 精确停止 24 个本轮算子容器；生成唯一当前 UID、单硬链接、`0400` 终态审计 `existing-containers.jsonl.paused.jsonl.audit.72a7b72a10334738852a2ff1507f8f44.jsonl`。维护锁可非阻塞获取，原 `ocr-v6-amd` 保持 `Exited(143)`；未生成镜像清理证据，未执行 prune、强制镜像删除、卷/数据/课程结果/Harness 证据删除。
- 结论：该 release 不得计入 OpenSpec 14.3-14.7。修复必须形成新完整 SHA，并以本 release 为同 tag 直接前驱重跑完整 8A.7；真实链路、容量稳定性、回滚演练、最终汇总和精确旧镜像清理仍待新 SHA 证据。

## 2026-08-21 - 8A.7 三路课程媒体可达性门禁

- 失败 release：`99e0f9aeca14fda1679410a31b05e57bac1e936e`；直接前驱为 `aae96b046dea1d724f8656c07ee7b5e89ac14d73`。
- 已通过门禁：clean-clone 六层验证、16 进程配置权威、四平台与八类算子镜像、PostgreSQL `0006`、24/24 实例注册、18/18 GPU 真实推理、6/6 CPU Smoke、八算子综合 Smoke，以及 deployment `76/76` 反例和 `17/17` 压力/恢复用例，共 `93/93`。
- 真实课程结果：ASR 转写、课程脑图和教师行为分析完成；PPT Slice 下载 P 视频时收到 HTTP `404` 并进入终态 `70`；学生视频准备持续收到 HTTP `404` 并保持等待重试。当前任务标识为 `m2b-v1.0_260812-99e0f9aeca14-full-course`。事后读取 PostgreSQL 的 `course_task_types.request_payload` 确认，本 release 的 PPT URL 缺少 T/S 所在课程目录中的时间片段，不能把 PPT 失败只归因于源站波动。
- 复核边界：PPT 失败导致 `PPT-012/013/014` 与 `KEY-005` 没有真实结果，独立复核按失败关闭处理，未发布 offline review request、外部 review index 或 `VIS-025`；本 release 不得计入 OpenSpec `14.3-14.7`。
- 现场对照：使用修正后的同课程 T/S/P 地址，从 Orchestrator 容器通过 stdin 只读探测，三路均返回 HTTP `206`、`Content-Length=1048576` 且首块长度为正。该结果只确认后续受控输入，不改写旧 `task_id` 已有失败终态。
- 恢复：服务器权威 release 中唯一恢复审计为 `existing-containers.jsonl.paused.jsonl.audit.f25ccdfe5eab4b6daa86061574653cbb.jsonl`，当前 UID 所有、权限 `0400`、单硬链接且内容为空；空审计与快照中的原 `ocr-v6-amd` 本就为 Exited 一致。24 个 `algorithm-operators` 测试容器均已停止；四平台和 PostgreSQL、Redis、Kafka、MongoDB 保持 healthy，原 `ocr-v6-amd` 保持 `Exited(143)`；未删除容器、镜像、卷、模型、数据或报告。
- 修复：新增 `preflight-course-media`，固定在 deployment 与课程提交之间，从 `orchestrator-service` 容器并发读取 T/S/P 首块；Canonical 固定三轮并全部要求 HTTP `200/206`、正声明长度和正读取长度。三路 URL 从 Canonical 到宿主预检、再到容器探针均通过 stdin 传递；外层连续 runtime 改由匿名受控脚本文件执行，不把完整正文放进 Bash argv 或可被子进程消费的 stdin。宿主逐角色对账探针摘要与实际输入，聚合器要求同一角色三轮摘要恒定。任一路失败、容器超时/不可用、stdout 为空/异常或退出码矛盾时，先原子记录不含 stderr、完整 URL 和媒体内容的脱敏失败证据，再返回非零、不创建新 `task_id` 并进入精确恢复。最终 aggregator 强制校验当前 release/SHA、通过状态、固定三轮、每轮恰好 T/S/P、摘要稳定以及逐项状态/长度，证据缺失或失败时不发布 `summary/cases.json`。该门禁不修改 `MediaDownloader` 的业务终态或增加无限下载重试。
- 本地验证：媒体门禁与 8A.7 生成顺序定向 `26 passed`，媒体/总控/聚合/锁边界定向 `584 passed`，平台全量 `2709 passed, 3 skipped, 27 warnings`；3 个 skip 只因本机缺少 Canonical FaceRec Token/容器，远端不得跳过，warnings 为既有多线程进程中 `fork()` 的 Python 弃用提示。Ruff、Mypy、Bash 语法、OpenSpec strict 和 `git diff --check` 通过；独立只读复审确认失败证据、URL 传递、摘要绑定、固定三轮和最终聚合门禁没有剩余阻断或中等风险。真实容器门禁及全泳道仍必须由新 SHA 的完整 Canonical 证明。

## Record template

- Date and scope:
- Previous state:
- Target state:
- Changed files:
- Contract impact:
- Verification command and environment:
- Evidence tier and verdict:
- Remaining risks:

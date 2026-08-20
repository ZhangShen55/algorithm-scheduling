# 统一算子配置、容量租约、在线 OCR 与镜像清理验证场景

## 当前状态

本场景对应 OpenSpec `unify-operator-capacity-leases-and-online-ocr`。2026-08-19 已进入 apply：
公共租约模型、Redis 原子容量、Control API、普通/工作项/VBas/在线租约客户端、在线单图 OCR、
八算子 TOML 配置和 Compose 静态合同已经实现。真实 Redis 下的跨服务容量、逐图 PPT、在线
容量耗尽、心跳差异和三类调用客户端跨 TTL 已取得自动化证据。最新失败 release
`ea39759ad8abb7d970bef386d1f1de0dd0391c71` 已通过 clean-clone 六层门禁、16 进程配置权威、
24 实例注册、18/18 GPU 真实推理、6/6 CPU Smoke、8/8 算子 full Smoke 和 PPT 三实例长视频
切片；217 条反例和 26 条压力用例也均实际执行，唯一失败为 `LOAD-011`。失败时任务事实和
Outbox 已创建，但 Orchestrator 从 Stage45 后持续 readiness 503，未生成 DAG。该 release 已
完成精确恢复且未清理旧镜像；新的 Stage45→deployment 稳定门禁仍须进入新 SHA 并完整重跑。
当前结论仍是“实现和已执行层级部分符合，最终 Canonical、全部业务泳道、B 级复核与精确旧镜像
清理待验证”，不得据此宣称整个变更完成。

本记录使用用户修改后的 OpenSpec 作为权威来源，固定以下新增约束：

- `platform.max_concurrent_requests` 和 `declared_capacity` 只允许正整数，不支持 `-1`、零值或其他类型。
- 八算子从 TOML `[platform]` 读取注册开关、Control Service 地址、心跳和容量，从
  `[runtime].require_gpu` 读取 GPU 强制检查；根配置与 2B 部署配置使用不同的已批准默认值。
- Compose 不再设置五个已迁移的平台/GPU环境变量或 `GPU_PROCESS_NAME`，但继续负责 Token、实例
  ID、服务 URL、物理 GPU/可见设备、配置路径、端口、worker、镜像、挂载、网络和资源限制。
- 所有可能跨越单次租约 TTL 的同步 HTTP 调用都必须设置有限硬超时，并周期续租同一个租约。
- Online Gateway 请求体上限为 `75497472` 字节（72 MiB），Base64 解码图片上限为
  `52428800` 字节（50 MiB）；OCR 的 `ocr.image_max_bytes` 同步为 `52428800`。
- `192.168.29.11` 的新 SHA 镜像完成 revision、替换、健康、24 实例注册和 Smoke 后，旧平台/算子
  镜像只允许按已核验的精确 ID 删除；基础、基础设施、原业务镜像、模型、数据和证据不得清理。
- 当前已经取得静态、单元、真实 Redis 和八算子项目测试证据；四服务运行、真实跨服务泳道、
  最终 SHA 的 24 实例重建与精确旧镜像清理仍是必须执行的门禁。

权威规划文件：

- `../openspec/changes/unify-operator-capacity-leases-and-online-ocr/proposal.md`
- `../openspec/changes/unify-operator-capacity-leases-and-online-ocr/design.md`
- `../openspec/changes/unify-operator-capacity-leases-and-online-ocr/specs/`
- `../openspec/changes/unify-operator-capacity-leases-and-online-ocr/tasks.md`

## 验证范围

### 1. 八算子统一 TOML/Compose 配置归属与正整数容量

| 算子 | 每实例默认值 | 一个平台工作单元 | 必须保留的本地约束 |
| --- | ---: | --- | --- |
| ASR Online | 10 | 一个 WebSocket 会话 | 流式模型处理约束 |
| ASR Offline | 4 | 一次音频转写请求 | `concurrency=5`、内部排队和模型串行锁 |
| FaceRec | 128 | 一次 `/recognize` 请求 | `thread.max_workers` Dlib 进程池 |
| OCR | 256 | 一张图片的一次 OCR 调用 | `ocr.max_concurrency=1`、引擎锁、50 MiB 单图限制 |
| ScreenDet | 128 | 一次 `/detect_all` 请求 | `max_batch_size` 单请求批量限制 |
| PPT Slice | 10 | 一个后台切片任务 | 统一字段同时作为本地任务上限 |
| VBas | 128 | 一次学生或教师图片批次 | `MaxConcurrentBatches`、`MaxQueueSize` 和模型保护 |
| Text Analysis | 256 | 一次脑图或关键词 HTTP 请求 | 接口内部 LLM 分片和并发 |

验收必须证明：

1. 八份根 TOML 均为 `registration_enabled=false`、空 Control URL、心跳 `5` 和
   `runtime.require_gpu=false`；八份部署 TOML 均开启注册、使用容器 Control URL，六类 GPU
   profile 强制 GPU、两类 CPU profile 不强制 GPU。根/部署 TOML 的确认容量保持一致。
2. 注册开关和 GPU 要求只接受严格布尔值；启用注册时 URL 必须合法；心跳必须是有限正数；
   容量的 `0`、负数、布尔值、浮点数和字符串均在接收业务流量和注册前失败关闭。
3. 全部 24 个受控实例不再设置 `PLATFORM_REGISTRATION_ENABLED`、
   `PLATFORM_CONTROL_SERVICE_URL`、`PLATFORM_HEARTBEAT_INTERVAL_SECONDS`、
   `PLATFORM_DECLARED_CAPACITY`、`REQUIRE_GPU` 或 `GPU_PROCESS_NAME`；对应类型级设置来自 TOML。
4. Compose 继续提供 Token、唯一实例 ID、容器 DNS 服务 URL、GPU ID/可见设备、`CONFIG_PATH`、
   端口和单 worker；YAML anchors 不能使任何字段在 `docker compose config` 展开后丢失或串实例。
5. 六类 GPU 算子不设置 `GPU_PROCESS_NAME` 时仍使用镜像入口脚本的确认默认进程名，并由真实
   `nvidia-smi`/容器证据归属到正确实例和物理 GPU。
6. VBas 多能力和 Text Analysis 两能力共享同一实例总池，不按 capability 复制容量。
7. 除 PPT Slice 的同义任务上限外，旧本地保护字段不能被统一平台字段覆盖。
8. 八算子既有业务路径、方法、字段、默认端口、模型目录和 PPT shared-path 与基线一致。

### 2. Redis 活跃租约与任务归属

平台分发占用只使用 `active_lease_count`；`reported_inflight` 只用于观测和差异告警。真实 Redis
验证必须覆盖：

- 短请求释放后，即使上一轮心跳仍然较高，槽位也能立即再次分配。
- 并发争抢最后一个槽位时恰好一个申请成功；同一实例的不同 capability 争抢同一集合。
- Redis `TIME` 生成 `acquired_at/expires_at`，续租保留获取时间和上下文，释放、过期、注销、
  重注册和 Redis `run_id` 变化均不遗留孤立租约。
- `work_context` 只包含 `source_service`、`work_type`、`work_id`、可选 `task_id/node_id/item_id/trace_id`，
  不接受 Base64、媒体、OCR/ASR 文本或额外业务正文。
- `POST /internal/operator-instances/lease/context` 对相同上下文幂等，对冲突绑定返回 `409`，对失效租约返回 `404`。
- `GET /ops/operator-instances/{instance_id}/active-leases` 清理失效成员，区分已绑定和未绑定租约，
  显示 `active_lease_count`、`reported_inflight` 和差异，但不猜测任务身份。
- 高频租约明细只存在 Redis；测试必须证明没有新增逐租约 PostgreSQL 写入或表。

### 3. 租约粒度、有限超时和周期续租

| 调用类型 | 租约边界 | 跨 TTL 行为 |
| --- | --- | --- |
| 普通离线节点 | 一次真实 HTTP 调用 | 有限硬超时内周期续租；终态、异常、超时或取消后释放 |
| 在线 HTTP | 一个同步请求 | 申请时绑定在线上下文；跨 TTL 续租；不进入队列 |
| 在线 ASR | 一个 WebSocket 会话 | 会话存续期间续租，关闭/断连后释放 |
| PPT Slice | 一个异步后台任务 | 从受理到终态持久化持续续租 |
| PPT OCR | 每个 `ppt_image_id` 一个 `ocr` 租约 | 每张图片独立选择实例并续租，不保留节点级 OCR 租约 |
| PPT 关键词 | 每个 `ppt_image_id` 一个 `extract_keywords` 租约 | 单项持久化后释放，不保留节点级 Text Analysis 租约 |
| VBas | 一个学生/教师图片批次 | 批次内多帧不拆租约，跨 TTL 续租 |

每种同步 HTTP 调用至少需要四条时序证据：请求完成前租约持续有效、跨 TTL 只续租原租约、
续租失败后不再派生工作、调用方停止续租后 Redis TTL 自动回收。HTTP 硬超时和租约 TTL
必须分别记录，不能用超长租约替代请求超时，也不能因一次 TTL 到期释放仍在执行的调用容量。

`PPT_OCR` 和 `PPT_KEYWORDS` 的协调节点不是算子工作单元。多图片用例必须同时证明没有外层
同能力租约、每个在途图片恰有一个租约、不同图片可以选择不同实例，且部分完成结果在容量
暂时不足时继续保留。

### 4. Online Gateway 单图 OCR 与图片边界

新增接口固定为：

```http
POST /api/online/ocr/recognize
```

请求使用必填 `image`、可选 `image_id` 和可选且默认 `false` 的严格布尔 `enable_formula`。
网关把单图转换成 OCR `/ocr/prediction` 的单元素 `key/value`，成功时在现有
`BusinessResponse.data` 中保留 `key`、`value`、`formula_results`、`err_no` 和 `err_msg`。

图片边界矩阵：

| 用例 | 预期 |
| --- | --- |
| 请求体大于 72 MiB | 网关在申请租约和 Base64 解码前拒绝 |
| 请求体不超过 72 MiB、解码图片大于 50 MiB | 网关在申请租约前返回 `40001` |
| 在线图片不超过 50 MiB | 可进入租约和 OCR 调用 |
| PPT OCR 直接调用算子且图片大于 50 MiB | OCR 按 `image_max_bytes=52428800` 拒绝 |
| 省略 `enable_formula` | 转发 `false` |
| 所有 OCR 实例满载 | HTTP `200`、业务码 `50301`，网关和 Control Service 均不排队 |
| OCR HTTP/响应格式失败 | 释放租约并返回业务码 `50000` |

在线与离线 OCR 使用同一个 `ocr` 能力池，不设置来源配额。确定性选择继续偏向排序靠前的
`ocr-gpu0` 是允许行为；验收关注共享原子容量，不把轮询均衡作为通过条件。

### 5. 兼容、部署回归和精确镜像清理

新增在线 OCR 不得破坏 VBas、FaceRec、ScreenDet、ASR Online 路由，也不得改变
`PPT_SLICE -> PPT_OCR -> PPT_KEYWORDS` 的图片身份、manifest、PostgreSQL 结果结构和终态回调。
最终部署验证必须覆盖八种 profile、24 个实例和里程碑 2B 的 PPT、ASR、教师/学生视觉及在线
泳道；不得只以算子 Smoke、类存在或健康接口替代跨服务证据。

在 `192.168.29.11` 构建前必须记录当前平台/算子镜像引用、精确 ID、revision、大小和所有容器
引用。最终 SHA 新镜像只有在完成 revision 校验、容器替换、基础健康、24 实例注册和算子 Smoke
后，才允许删除无容器引用且能由本工作区 Compose 槽位和旧 release revision 共同证明身份的旧
平台/算子镜像。任一新版本门禁失败或旧镜像仍被运行中、暂停、停止容器引用时必须跳过删除。

清理不得使用 `docker image rm -f`、未解析变量、宽泛匹配、`docker system prune` 或删除 Docker
数据目录；不得触碰 CUDA/Python 基础镜像、PostgreSQL/Redis/Kafka/MongoDB、原业务
`ocr-v6-amd`、模型资产、数据卷、`/data/course`、`/data/result` 和历史 release/Harness 证据。
报告必须记录删除前后清单、逐项原因、实际删除 ID 和释放空间。清理后旧版本即时本地回滚不再
可用，旧 Git SHA、配置和证据必须保留，以便重新构建或从可信镜像源恢复。

## 规格到证据矩阵

| 规格能力 | 主要代码 | 自动测试 | 最低运行证据 | 当前结论 |
| --- | --- | --- | --- | --- |
| `unified-operator-capacity` | `packages/operator_registry_client/config.py`、八算子配置/入口、`deploy/docker-compose.operators.yml` | `test_operator_registry_client.py`、`test_operator_deployment_integration.py`、`test_milestone_2b_operator_configs.py`、`test_operator_config_authority.py`、八算子项目测试 | 16 个独立进程的根/部署 TOML 权威对照、Compose 展开配置、真实推理、24 实例、精确镜像清理 | 本地配置/项目测试和进程级权威探针符合；最终镜像和清理待验证 |
| `attributed-capacity-leases` | `platform_common/redis_operator_registry.py`、Control 租约 API、三个调用服务的容量客户端 | Redis 集成、Control API、dispatcher/executor、PPT 工作项、VBas 客户端和 `test_unified_capacity_cross_service.py` | 真实 Redis 并发、跨 TTL 时序、PPT/ASR/VBas 跨服务链路 | 真实 Redis/Control/契约算子层符合；真实算子完整泳道待验证 |
| `online-ocr-routing` | `online_gateway_service/app/api/routes.py`、`request_validation.py`、`capacity.py` | `test_online_gateway.py`、`test_unified_capacity_cross_service.py`、OCR 项目测试 | 契约 OCR、真实 OCR、72/50 MiB 边界、在线/离线同池并发 | 契约 OCR 与真实 Redis 同池符合；真实 OCR 跨服务待验证 |
| 兼容与交付 | 八算子业务入口、四服务 README、总体设计、部署/Harness 脚本 | 路由合同、Harness 一致性、平台/算子全回归 | 四服务运行、全部泳道、最终 SHA 不可变报告 | 文档实施中；最终发布证据待验证 |

任何矩阵行在只有静态代码、模拟成功响应或健康检查时都不得改为“符合”。真实 Redis、服务运行、
算子契约和三卡部署证据必须分别注明层级，跳过项不能计为通过。

## 2026-08-19 apply 中间证据

当前工作树已取得以下可复现结果，最终提交后必须以完整 Git SHA 重新记录：

```text
公共配置/Compose/注册预检/wheel 合同：118 passed
PPT OCR/关键词失败、取消、部分完成和恢复：4 passed
八算子项目测试：ASR Online 22、ASR Offline 58、FaceRec 54、OCR 175、
                 ScreenDet 78、PPT Slice 100、VBas 75、Text Analysis 25
Control 注册 API：37 passed
Orchestrator 普通节点定向测试：18 passed
GPU evidence 与报告聚合：657 passed
统一容量跨服务 + Online Gateway：36 passed
四个根服务独立运行：Control 21、Orchestrator 46、Vision Orchestrator 8、Online Gateway 9 passed
里程碑 2A 四泳道完整运行与恢复回归：1 passed
```

这些结果分别属于静态、单元、真实 Redis 跨服务和项目级验证；契约算子只替代模型推理，
不替代真实 OCR/ASR/VBas 等算子、课程业务泳道和 `192.168.29.11` 最终 SHA 门禁。

四个根服务的测试必须分别从 `control_service/`、`orchestrator_service/`、
`vision_orchestrator_service/`、`online_gateway_service/` 根目录运行。把四个 `tests/` 一次性从
平台目录收集会因服务使用各自顶层 `app` 包而产生 `ModuleNotFoundError: app`；这属于错误命令，
不是服务实现失败。

同一工作树还完成了下列本机运行验证；记录时的父 revision 为
`bd59541`，最终提交后仍需用新的完整 SHA 生成不可变报告：

```text
平台核心定向门禁：147 passed
平台完整回归首轮：2575 passed、4 failed、3 skipped；4 个失败均为新增部署门禁与既有测试替身
  不同步，修复后定向 4 passed，部署脚本完整套件 303 passed。
平台完整回归复跑终态：2579 passed、3 skipped、27 warnings，耗时 561.68 秒；3 个跳过项
  仅为未提供 `OPERATOR_REGISTRY_TOKEN` 时不执行 canonical FaceRec 集成，不属于功能失败。
真实 PostgreSQL/Redis/Kafka 集成：59 passed
四服务：Control、Orchestrator、Vision Orchestrator、Online Gateway 同时启动；健康检查通过；
  Control PostgreSQL/Redis/schema ready；Orchestrator 的 Outbox、课程消费、节点执行、PPT reconcile
  四个后台循环及 PostgreSQL/Kafka/Control 依赖均 ready；五个进程（含契约算子）均优雅退出。
真实 Control 租约 API：申请 10 秒租约 -> 绑定上下文 -> 查询 active_lease_count=1 ->
  续租为 30 秒且 acquired_at/work_context 不变 -> 释放 -> active_lease_count=0。
ASR Online：22 passed；8084 健康；真实 WAV WebSocket 会话得到非空增量结果。
ASR Offline：58 passed；8083 健康；真实 WAV 返回非空结果；6 路并发观察到本地
  max_processing=5、max_queued=1，未被平台注册容量 4 覆盖。
FaceRec：54 passed；8003 就绪；真实图片识别成功且未保存人脸原图。
OCR：175 passed；由于其他本机应用占用 IPv4 127.0.0.1:8866，算子在 IPv6 回环 [::1]:8866
  启动；版本和真实图片 Smoke 通过；declared_capacity=256，本地 max_concurrency=1；
  52428800 字节进入格式解析，52428801 字节在推理前拒绝。
ScreenDet：78 passed；8880 ready；真实图片 /detect_all 的 tilt、screen、quality_abnormal、
  occlusion 四模块执行成功，failed_modules 为空。
PPT Slice：100 passed；9001 健康和版本接口正常；本地/平台注册容量均为 10；24 秒六页面
  合成视频受理后成功发送一次终态回调，status=60、count=3，三张切片和 manifest 均位于
  /tmp 测试结果根的固定 task_id/ppt 路径，未发现 .part 残留。
VBas：从既有 Python 3.11 `vbas` 环境克隆规范名 `jy-tias`；75 passed、pip check 通过；8981
  模型就绪；学生真实图片返回 3 人，教师真实图片返回教师及行为结果；共享容量 128，本地
  max_concurrent_batches=1、max_queue_size=0，学生请求执行中并发教师请求得到 429，完成后重试成功。
Text Analysis：从既有 Python 3.11 `openai` 环境克隆规范名 `ai_report`；25 passed、pip check
  通过；8000 启动并保留 27 条路由；真实 `/v1/extract_keywords` 与 `/v1/course_overviews`
  均返回 200 和结构化结果；两个平台注册能力共享 declared_capacity=256。
```

八算子配置权威探针通过 16 个独立 Python 子进程逐一加载受版本控制的本地安全 TOML 和受控部署
TOML；每个子进程都先确认五个已迁移旧环境变量已经注入，再通过显式 `CONFIG_PATH` 调用对应
算子的正式配置加载入口。FaceRec、OCR 和 Text Analysis 使用不含真实凭据的安全模板，探针不依赖
被 `.gitignore` 排除的运行配置；OCR 仅跳过与配置权威无关的模型目录存在性检查。本地配置必须解析为“不注册、不强制 GPU”，部署配置必须解析为
“启用注册、容器 Control URL、心跳 5 秒、确认容量和对应 GPU 要求”，旧环境变量不得覆盖任何值。
最终提交后仍须以完整 Git SHA 重跑并原子保存证据；远端业务泳道、压力/回滚和精确镜像清理
继续按各自门禁执行。

## 2026-08-20 最终门禁真实性修正

复审发现三类证据可能产生伪阳性：clean-clone 曾把依赖不可用导致的 skip 仍写成集成通过；
业务 Campaign 曾用一个阶段结果批量生成 `79/28/34/9` 条通过记录；旧镜像清理只校验注册和
Smoke。当前实现改为：

- PostgreSQL/Redis 与 Kafka 各自输出 JUnit 统计，必须 `tests>0` 且
  `failures=errors=skipped=0`；clean clone 统一安装 `.[dev]`。
- 16 进程配置权威证据固定写入 release `preflight/operator-config-authority.json`；同 SHA 续跑
  只复用权限、字段、SHA、配置矩阵和 16 条结果全部严格匹配的已有证据。
- 150 条业务用例逐案保存 `check_id`、真实泳道探针和显式 case-to-test 语义映射；
  映射指定的每个测试模式都必须在当期 JUnit 中实际通过，禁止按 `JOB/FILE/PPT/...`
  前缀机械复用整组结论。8 个质量项还必须提供当前 release 的独立 B 级复核。
- 新增 `run-milestone-2b-8a7` 总控。镜像删除前强制校验 clean-clone、配置权威、217 条反例、
  26 条压力、最终报告“通过”、28 个健康容器最终 SHA 及仍有效的维护锁。
- Canonical 异常退出不只释放维护锁：在安全账本可证明时，停止本轮精确算子集合并恢复
  已授权的原业务；账本或身份异常时失败关闭，不停止未证明容器。算子精确恢复 trap
  只在所有账本/容器核验函数定义后替换外层 trap，以保证阶段 3 早期失败仍能恢复原业务。
- `LOAD-007` 不再把“请求偏向排序靠前实例”当作错误；测试必须证明偏向可以存在，但排序靠前实例满容量后会选择下一可用实例，且总容量不超卖。

本轮本地定向验证为：配置/clean-clone/清理门禁 `23 passed`，业务逐案与 8A.7 总控
原回归 `27 passed`，媒体失败关闭、显式映射和总控定向 `30 passed`；四阶段映射 JUnit
分别为 `99/57/36/138`，零失败、零错误、零跳过且无缺失映射；Ruff 通过。最终 SHA 的远端业务、容量、恢复、243 条汇总及镜像删除尚未执行，
因此 OpenSpec 12.9、14.1、14.3-14.7 仍保持未完成。

`b0f5ae68cae4d50349d85b43f851bb4eb47e3424` 的首次最终 8A.7 在阶段 1 调用
`release-image-cleanup snapshot` 时因包装器以文件路径启动 Python、无法解析平台 `scripts`
包而失败；该轮已在构建/替换算子前终止。修复后包装器必须使用 clean-clone
准备的 `.venv/bin/python -m deploy.scripts.release_image_cleanup`，并以新 SHA 重跑全部门禁；旧失败
release 不得补写为通过。

`448f6f3f21e748fc6f9ce5b05dbcdabae82b96b3` 的最终 8A.7 在阶段 1 clean-clone 全量测试失败，
结果为 `2647 passed, 5 failed, 6 skipped`，因此同样未进入镜像构建、容器替换或业务泳道。
其中三项失败证明 `run_milestone_2b_case_batch.py` 以文件路径直接执行时无法解析平台
`scripts` 包；另外两项失败来自早期失败测试继承了 Canonical 的绝对
`PREVIOUS_RELEASE_ROOT`，在临时项目中被正确的同 release tag 路径校验提前拒绝。修复要求是：
批次 runner 在导入平台包前显式加入自身项目根；测试环境显式清空与测试目标无关的前驱变量。
本轮 Canonical 输出 `restore: complete`；维护锁已释放，原 `ocr-v6-amd` 保持执行前的 Exited
状态，PostgreSQL、Redis、Kafka、MongoDB 和四个平台容器均恢复为 healthy。修复必须进入新
Git SHA 和新不可变 release，旧失败 release 不得补写或计入 OpenSpec 12.9/14.x 证据。
修复后本机聚焦回归 `5 passed`，平台全量 `2655 passed, 3 skipped`，四个根服务分别为
`21/53/16/20 passed`；Ruff、strict Mypy、compileall、无 `PYTHONPATH` 文件入口、OpenSpec
strict 和 `git diff --check` 均通过。3 个本机 skip 只因未提供 canonical FaceRec 注册令牌，
不得作为远端三卡证据。

`7df1c212dc219c1422b5ba857cbd426b1f3e1da5` 随后的最终 8A.7 已越过上述平台全量失败，
但在 clean-clone 的真实 PostgreSQL/Redis JUnit 统计门禁失败。pytest 生成的标准结构为
`<testsuites><testsuite .../></testsuites>`，旧解析器只读取根节点属性，因而把实际成功执行的
子 suite 误判为零用例。本轮仍在镜像构建、容器替换和业务泳道前终止，并输出
`restore: complete`；维护锁释放、原 `ocr-v6-amd` 保持 Exited、基础设施和四个平台容器均为
healthy。修复后的解析器必须支持根 `testsuite`、根带完整汇总的 `testsuites`，以及 pytest
根无汇总属性时对子 suite 的严格求和；字段缺失、非整数、负数、零用例、失败、错误或跳过
继续失败关闭。修复必须进入下一 Git SHA 和新不可变 release。
修复后 JUnit 聚焦测试 `10 passed`，真实 pytest XML 解析得到
`tests=10/failures=0/errors=0/skipped=0`，平台全量为 `2658 passed, 3 skipped`；Ruff、strict
Mypy、compileall、OpenSpec strict 和 `git diff --check` 均通过。3 个 skip 的远端禁止复用边界
保持不变。

`7b7d135cc042b81da45000df4297d4f993723d54` 的最终 8A.7 已通过 clean-clone 与 16 进程配置
权威门禁，并成功构建、检查当前 SHA 的八类算子镜像；随后在启动任何新算子容器前，
`resolve-operator-ledgers` 报告 `no complete operator ledger ancestor`。现场只读审计证明合法链为
`7df1c21 -> 448f6f3 -> b6706fc`，`b6706fc` 保存完整 baseline/new；前两个候选均为已经通过
唯一 `0400` 终态 audit 的 completed direct maintenance，并具有合法 predecessor marker。
resolver 只允许活动 `direct` 沿 marker，却没有处理同样经过严格验证的 `completed` direct，
因此错误停止。修复必须允许两类合法 direct 状态沿 marker 只读回溯，同时继续拒绝缺 marker、
partial、环和账本不一致。该轮没有替换任何平台/算子容器，没有清理旧镜像，退出时
`restore: complete`、维护锁释放、原 `ocr-v6-amd` 保持 Exited，基础设施和四平台服务 healthy。
修复后 resolver 聚焦测试 `10 passed`、完整阶段 3/task9 合同 `248 passed`、平台全量
`2660 passed, 3 skipped`；Ruff、strict Mypy、compileall、OpenSpec strict 和
`git diff --check` 均通过。3 个本机 skip 仍只因未提供 canonical FaceRec 注册令牌。

## 2026-08-20 远端预验收失败与修复

`192.168.29.11` 曾以父提交
`b0012b513cdb0548d9ff37b2b5da98f057a76859` 启动 Canonical 预验收。ASR Offline
镜像构建完成后，ASR Online 在 Dockerfile 的构建期应用导入门禁失败：镜像声明正式
`config.toml` 仅由 Compose 在运行时挂载，但 `RUN python -c "from app.main import app"` 在
构建阶段仍按默认路径读取 `/app/config.toml`。该文件不存在，因此 runner 在替换 ASR Online
tag 前退出；这次执行不得计入 OpenSpec `9.5` 或 `14.x` 完成证据。

修复后的 ASR Online Dockerfile 在同一个 `RUN` 层创建空的临时 TOML，以显式
`CONFIG_PATH` 完成导入后立即删除；根配置仍不进入镜像，运行时仍使用 Compose 的只读挂载。
本机取得以下回归证据：

```text
ASR Online 打包合同：15 tests OK
临时普通 TOML 下 from app.main import app：通过
平台镜像配置排除与 Miniconda 合同：3 passed
```

远端必须改用包含此修复的后续完整 Git SHA 重新执行 Canonical 门禁；父提交的失败 release
只作为诊断证据保留，不得覆盖或伪装为成功报告。

## 2026-08-20 最终门禁平台迁移与恢复缺陷

`76aa93a37a5e801aadcdd46a47e6e1bb76bf8f8c` 已通过 clean-clone、真实依赖 JUnit、16 进程配置
权威和八类算子镜像 revision 门禁，但平台替换时 Control readiness 报告缺少
`course_task_types.submission_id`。Canonical 未在平台启动前应用 `0006` 是第一原因；随后
异常恢复又因 `EXIT` trap 过早删除 Compose service allowlist，无法验证 new ledger 身份，
暴露第二个失败恢复缺陷。本轮没有启动 24 个新算子或进入业务 Campaign，旧镜像未清理。

现场先按完整 ID、Compose project/service、权威 24 项清单和 Exited 状态核验 new ledger，
再使用 canonical restore 完成唯一只读 audit；终态为 `restore: complete`，维护锁释放，原
`ocr-v6-amd` 仍为 Exited。修复后平台启动前必须幂等执行并严格核验 `0006`；临时 allowlist
必须保留到精确停止和 restore 完成后才能清理。聚焦回归为 `5 passed` 且迁移脚本 Bash 语法
检查通过；仍需用新 SHA、以本 release 为立即前驱重跑完整 8A.7。

## 2026-08-20 最终门禁 PostgreSQL 目录漂移

`0d8ee4af910b739e3bbca90c8088986e3920bc7a` 已越过 clean-clone、16 进程配置权威、八镜像
revision、`0006` 幂等迁移和四平台健康门禁，但 runtime preflight 的独立数据库列目录未包含
`course_task_types.submission_id`，因此与真实 PostgreSQL 严格对账失败。本轮尚未启动新算子或
进入业务 Campaign；Canonical 已验证继承账本、精确停止并输出 `restore: complete`。

修复必须同时更新部署 preflight 和测试夹具，并增加与 Control Service readiness
`CONTROL_SCHEMA_COLUMNS` 的集合一致性回归，防止后续前向迁移再次只更新服务健康检查而遗漏
部署门禁。仍需用新 SHA、以本 release 为立即前驱重跑完整 8A.7。

## 2026-08-20 最终门禁业务 Campaign 参数污染

`97b9b079325505d8858cfd8dc5649d0a2f2f342d` 完成模型资产、不可变目录、宿主机预检、容器快照和
基础设施健康后，在总控展开命令中发现四个业务 Campaign 的每个参数前存在字面量 `+`。最小
执行复现证明这些字符会作为未声明 argv 传入，而不是无害的日志格式。为避免完成八镜像构建后
才确定性失败，本轮在 clean-clone 测试期间终止；随后以精确 snapshot/paused ledger 执行
Canonical restore 并输出 `restore: complete`，没有替换平台或算子容器。

修复应删除连接符中的补丁标记字符，并通过真实 shell 执行生成的离线 Campaign 命令、捕获和
逐项比较 argv。仅搜索阶段名称或执行 `bash -n` 不足以关闭该回归。仍需用新 SHA、以本失败
release 为立即前驱重跑完整 8A.7。修复后的 argv 聚焦回归为 `5 passed`，8A.7 总控、部署脚本
和 Task 9 完整回归为 `558 passed`；Ruff、strict Mypy、OpenSpec strict 与差异检查均通过。

## 2026-08-20 视觉长视频抽帧资源门禁

最终 SHA `ecadb0cb1e884f24c18aa77965d5695101931d2f` 已越过八算子 full Smoke、deployment 反例和
26 条压力用例，并在真实 full-course offline Campaign 中完成 ASR 与课程脑图。教师/学生视觉
粗扫随后触发 Vision Orchestrator 的 `4G` cgroup OOM：dmesg 记录多个 ffmpeg 被 SIGKILL，
`/ready` 明确报告 consumer 后台循环退出，而旧 Compose `/health` 探针仍返回成功。

修复必须同时满足：`media.max_concurrent_processes` 为正整数且默认 `2`；T/S 两类抽帧共享同一
服务进程级信号量；全部时间点最终都被处理；Compose 使用 `/ready`。本地单元测试只证明并发
上限和配置/探针合同，最终结论还必须由下一不可变 release 在相同 T/S 长视频、`4G` 内存限制下
运行并核对 dmesg、任务终态和 `/ready`。失败 release 已按 baseline/new 和权威 Compose 身份
精确停止 24 个新算子并输出 `restore: complete`；不得把该轮写成 14.3 完成。

## 2026-08-20 视觉容量等待与启动顺序门禁

`c07df67910558716985941bb2feff73b637bd844` 已通过 clean-clone、16 进程配置权威、八算子/四平台
镜像构建及 `0006` 迁移。由于 Kafka 中存在上轮未完成视觉命令，平台先于 VBas 启动时
Vision Consumer 申请租约得到 HTTP `503`。旧实现让后台循环退出，`/ready` 因此正确返回
`503`，但该业务条件本应是离线等待而非服务故障。

验收必须证明：只有“暂无可用算子容量” HTTP `503` 被分类为等待；当前 Kafka offset 在容量可用前不提交；Consumer 按
`worker.poll_interval_seconds` 重试且保持存活；`/ready` 在等待期间保持就绪；关闭时不丢失消息。
注册中心不可用等其他 `503`、HTTP `400/401`、非法响应和其他协议错误仍必须让后台循环和 `/ready` 失败。该轮已完成
`restore: complete`，24 个新算子容器均未运行，非阻塞 `flock` 确认维护锁已释放；仍需在新 SHA
的完整 8A.7 中重新取得运行证据。

## 2026-08-20 视觉候选窗口与 Canonical 中断恢复门禁

`bec262b46bd7f570e43dc1a74b5f7e336f935084` 已完成 clean-clone、16 进程配置权威、
八类算子/四平台镜像、24 实例注册、18 个 GPU 实例逐一真实推理、6 个 CPU
Smoke、八算子综合 Smoke，以及 PPT/ASR 两条真实泳道。视觉泳道粗扫产生
`31` 个候选窗口，超过旧默认值 `20`，Consumer 因此退出。该 release 不得计入
OpenSpec 14.3 完成。

默认 `scan.max_candidate_windows` 调整为 `128`，但不删除上限；
`scan.max_detection_points=10000` 继续独立失败关闭。本地至少要求：

```bash
PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q \
  tests/test_adaptive_vision_scan.py \
  tests/test_run_8a7.py
```

候选窗口回归必须证明 31 个窗口可完整进入加密扫描、129 个窗口仍被有界保护拒绝。
Canonical 中断回归必须向 Python 总控发送 `SIGINT`，并证明 release-tag 锁持有进程
在外层 Bash `EXIT` trap 开始时仍存活、其他长子进程已终止，且 trap 在总控退出前完成；
不得仅检查字符串中存在 trap。

现场已按 `bec262b...` 的精确 `baseline=0/new=24` 账本、Docker 完整 ID、
`algorithm-operators` project 和权威 24 项 service allowlist 停止本轮容器，并以唯一
`0400` audit 完成 restore。终态为 24 算子均未运行、原 `ocr-v6-amd` 保持
`Exited(143)`、维护锁释放；未清理镜像。修复后必须用新 SHA，并将该失败
release 作为 `PREVIOUS_RELEASE_ROOT` 完整重跑 8A.7。

## 2026-08-20 B 级复核时序与早期中断恢复门禁

`3880772431313e45406e56601f5bbaabe951b039` 的 fresh Canonical 在 clean-clone 执行期间被主动
中断，因为只读复审确认旧流程要求 `--manual-review-json` 在 offline Campaign 进入前已经存在，
但当前 SHA 的 7 个离线复核项只能在四条真实任务完成后生成，`VIS-025` 又必须等待视觉结果；
继续执行必然在 `_build_case_checks()` 失败，旧 SHA 的 7 项证据也不得复制。

该轮中断早于算子账本初始化，没有创建本轮算子容器；精确现场为 `operator_running=0`、原
`ocr-v6-amd=Exited(143)`，平台与 PostgreSQL/Redis/Kafka/MongoDB 保持运行。使用该 release
唯一 snapshot 和空 paused ledger 执行权威 restore，输出 `restore: complete` 并生成唯一
`0400` audit `existing-containers.jsonl.paused.jsonl.audit.fc0d303c76cd4a8d97e0cf0614fc0af8.jsonl`；
没有执行镜像清理或 prune。由于 Python 总控的本次信号退出没有自行产出 audit，信号修复仍须由
下一 SHA 的自动回归和真实 Canonical 中断门禁重新证明。

修复后的 Campaign 在 offline/vision 各自真实结果产生后发布当前 SHA 与课程 `task_id` 的
write-once 复核请求，并有界等待 Git 外受限索引。逐项证据必须验证
`case_id/git_sha/task_id/status/reviewer/observed`，索引和证据必须为当前 UID、`0600`、单硬链接
且祖先无符号链接；普通 release 不保存课程图片、联系表或识别全文。只有 8 项全部由当前 release
独立产生后，243 项聚合才允许继续。

## 2026-08-20 Stage45 到 deployment 的运行时稳定门禁

`ea39759ad8abb7d970bef386d1f1de0dd0391c71` 在 Stage45 结束前已经完成四平台健康、24 实例
注册与全实例 Smoke，并输出 `CODEX_STAGE45_COMPLETE failures=0`；但 deployment 用例开始时
Orchestrator 已持续 `/ops/readiness=503`。`LOAD-011` 随后停止三个 ASR Offline 实例并提交
课程任务，任务事实和 Outbox 成功落库，却因后台运行时未消费而没有 DAG。该链路证明
“此前健康”不能替代 mutation 阶段之间的即时稳定门禁。

8A.7 总控因此固定以下顺序：

1. Stage45 必须以零失败结束。
2. 查询 `http://127.0.0.1:18101/ops/readiness`。
3. 仅当查询失败时精确执行 `restart orchestrator-service`，随后以 Compose `--wait` 等待该
   服务健康；不得重启其他三个平台服务或四类基础设施。
4. 无论是否发生重启，都重新执行绑定当前完整 Git SHA 的 `preflight runtime`。
5. 以上全部成功后，才允许启动 deployment 用例。

该门禁只恢复通用调度后台运行时，不把业务失败隐藏为重试。精确重启、健康等待或 runtime
preflight 任一步失败时，Canonical 必须保留非零状态并走既有精确 new ledger 恢复。失败
release 的唯一 `0400` restore audit 和未执行业务 Campaign 的事实保持不可变；新 SHA 的完整
Canonical 通过前，OpenSpec `12.9`、`14.1`、`14.3-14.7` 继续为未完成。

## 实施后验证入口

从工作区根目录执行 OpenSpec 门禁：

```bash
openspec validate unify-operator-capacity-leases-and-online-ocr --type change --strict --no-interactive
```

从 `algorithm-scheduling-platform/` 执行平台定向门禁：

```bash
PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q \
  tests/test_operator_registry_client.py \
  tests/test_redis_operator_registry_unit.py \
  tests/integration/test_redis_operator_registry.py \
  tests/test_operator_registry_api.py \
  tests/test_operations_api.py \
  tests/test_node_dispatcher.py \
  tests/test_ppt_text_pipeline.py \
  tests/test_vbas_batch_client.py \
  tests/test_online_gateway.py \
  tests/integration/test_unified_capacity_cross_service.py \
  tests/test_operator_deployment_integration.py \
  tests/test_milestone_2b_operator_configs.py \
  tests/test_harness_consistency.py
```

四个平台服务和八个算子的完整项目测试、启动、健康/就绪、路由对比与真实推理命令以根
`AGENTS.md`、各项目 `AGENTS.md` 及 OpenSpec tasks 第 11、12 节为准。里程碑 2B 远端验证仍须
使用受控脚本和新完整 Git SHA 的不可变 release，不得覆盖既有 `1aa5da67...` 证据。

## 证据目录合同

实施证据写入 Git 忽略目录：

```text
harness/reports/unified-operator-capacity-leases-and-online-ocr/{完整GitSHA}/
```

至少包含：

```text
metadata.json
openspec-validation.txt
route-baseline.json
operator-capacities.json
operator-platform-configs.json
compose-rendered-config.json
gpu-process-defaults.json
redis-lease-integration.txt
active-leases-sanitized.json
http-timeout-renewal.json
online-ocr-boundaries.json
image-inventory-before.json
image-cleanup-result.json
disk-usage-before-after.json
operator-local/
cross-service/
milestone-2b/
summary.json
```

`metadata.json` 必须记录完整 Git SHA、UTC/本地时间、主机、Python/依赖和容器 revision；每份
运行证据必须能追溯到同一 SHA。普通日志和报告不得记录令牌、密码、Authorization、Base64、
图片/音频、OCR/ASR 文本或外部模型密钥。大图片边界测试使用可生成的无敏感合成数据，不提交
50 MiB 图片到 Git。

## 完成门禁

只有同时满足以下条件，本场景和 `DEC-025` 才能从“待验证”改为“符合”：

1. OpenSpec 88 项任务均由对应代码和可复现证据关闭，严格校验通过。
2. 三份规格的全部场景都有自动测试或明确的运行证据，不存在用健康检查代替业务路径的结论。
3. 真实 Redis 证明活跃租约权威、共享池原子性、上下文、查询、续租、过期和 Redis 世代行为。
4. 三个调用服务证明有限 HTTP 超时与短 TTL 独立，跨 TTL、续租失败、取消和调用方失联均收口。
5. 在线 OCR 的 72 MiB/50 MiB 双边界、默认公式开关、错误码和在线/离线同池竞争全部通过。
6. 八个算子完成规定环境的编译、导入、测试、启动、路由与真实推理；本地保护语义无回归。
7. 根/部署 TOML、Compose 源文件及展开结果、全 24 实例注册值、GPU 默认进程名、镜像 revision、
   Smoke 和里程碑 2B 业务泳道取得同 SHA 证据。
8. 精确镜像清理只删除已批准旧平台/算子镜像，保留所有排除对象，并记录删除 ID 与释放空间；
   新版本门禁失败、镜像仍被引用或身份无法证明时均未执行删除。
9. 没有新增逐租约 PostgreSQL 写放大、孤立 Redis 租约、业务接口破坏或越界清理。

截至本记录创建时，上述运行门禁均未执行，因此不得引用本文件宣称功能已经实现或验收通过。

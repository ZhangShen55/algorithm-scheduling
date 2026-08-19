## 1. 基线与共享契约

- [x] 1.1 记录八个算子改造前的业务路由/方法、默认端口、根/部署 TOML 的平台与 runtime 字段、Compose 注册/GPU环境变量和受控部署注册值，形成可自动对比的兼容基线，并确认不触碰现有未跟踪文件。
- [x] 1.2 在 `platform_common.operator_registry` 增加统一容量值校验、`WorkContext`、扩展后的 `CapacityLease` 和活跃租约查询结果模型，约束上下文只接受声明的短标识字段。
- [x] 1.3 更新 `OperatorInstance`、`OperatorOpsStatus`、Control Service 请求模型及序列化边界，使 `declared_capacity` 只接受正整数，并覆盖 `0`、负数、布尔、浮点和字符串的拒绝测试。
- [x] 1.4 更新 `operator_registry_client.install_operator_runtime`，要求调用方显式传入已解析的 TOML 注册开关、Control Service 地址、心跳和容量，删除 `PLATFORM_REGISTRATION_ENABLED`、`PLATFORM_CONTROL_SERVICE_URL`、`PLATFORM_HEARTBEAT_INTERVAL_SECONDS`、`PLATFORM_DECLARED_CAPACITY` 读取；继续仅从部署环境读取 Token、实例身份、服务 URL、发布版本和实例标签。
- [x] 1.5 更新公共包导出、README、版本和 wheel 构建/安装测试，覆盖完整平台配置的严格类型、启用注册时地址校验以及保留实例环境变量的兼容性，确认八个算子镜像可以取得新模型且没有依赖临时 `PYTHONPATH`。

## 2. Redis 容量租约核心

- [x] 2.1 重写 Redis 申请 Lua：使用 Redis `TIME` 清理过期/旧运行标识租约，只以活跃租约数判断正容量实例是否可分发，并拒绝任何未通过正整数容量校验的实例记录。
- [x] 2.2 在申请 Lua 和租约解析中原子写入/返回 `acquired_at`、`expires_at` 与可选 `work_context`，确保同一 `instance_id` 的不同 capability 继续共享一个租约有序集合。
- [x] 2.3 增加上下文绑定 Lua 和 registry 方法：有效租约首次绑定成功、相同上下文重复绑定幂等、不同上下文改绑冲突、过期/释放/旧运行标识租约返回未找到。
- [x] 2.4 增加按实例列出活跃租约的原子清理和读取方法，返回绑定状态、上下文、时间、`active_lease_count`、`reported_inflight` 及不可伪造为任务的差异值。
- [x] 2.5 更新续租、释放、注销和重注册脚本，验证续租保留 `acquired_at/work_context`，所有终止路径清除租约明细且不会遗留孤立上下文。
- [x] 2.6 扩充 Redis 单元测试，覆盖陈旧高心跳不阻塞已释放槽位、共享池跨能力争抢、非法容量记录不可分发、实例排序偏向允许、过期清理和上下文全生命周期。
- [x] 2.7 使用真实 Redis 增加并发集成测试，证明最后一个槽位只成功一次、不同来源共享 OCR 池、Redis 重启标识隔离以及查询计数与原子集合一致。

## 3. Control Service 接口与运维查询

- [x] 3.1 扩展 `POST /internal/operator-instances/lease` 请求，使其可选携带 `work_context`，保持原有只传 `capability/ttl_seconds` 的客户端兼容。
- [x] 3.2 实现 `POST /internal/operator-instances/lease/context` 及明确的成功、`404` 和 `409` 映射，沿用现有内部服务访问边界并为非法/超长/额外上下文字段返回确定错误。
- [x] 3.3 实现 `GET /ops/operator-instances/{instance_id}/active-leases`，区分实例不存在、已过期租约、未绑定租约和已绑定但可能在算子内部等待的租约。
- [x] 3.4 扩展 `AuditedOperatorRegistry`、不可用 registry、协议和工厂接线，使新增方法在真实 Redis 与依赖故障路径均有一致行为，且不增加逐租约 PostgreSQL 审计写入。
- [x] 3.5 更新实例运维汇总、指标和日志口径：`schedulable_used=active_lease_count`，`reported_inflight` 只展示差异，日志不得包含图片、音频、OCR/ASR 文本或完整请求。
- [x] 3.6 扩充 Control Service API、审计装饰器和运行时测试，覆盖旧租约请求兼容、新接口 OpenAPI、鉴权边界、Redis `503`、绑定冲突与活跃任务查询。

## 4. Orchestrator 普通节点租约

- [x] 4.1 扩展 `ControlLeaseClient`，支持申请时传上下文、申请后绑定上下文、统一解析新租约字段，以及在有限 HTTP 硬超时内对可能跨越单次 TTL 的同步调用自动续租，同时兼容无上下文响应。
- [x] 4.2 为通用调度器增加节点级/工作项级租约作用域；普通节点继续先取租约再领取，工作项协调节点不得持有覆盖整节点的同能力租约。
- [x] 4.3 普通节点领取成功后立即绑定 `source_service`、`work_type`、`work_id`、`task_id`、`node_id` 和 `trace_id`；无节点、领取异常或绑定失败时正确释放并避免执行未归属调用。
- [x] 4.4 核对 ASR 转写、`course_overviews` 和同步普通适配器，确保一次真实 HTTP 调用只占一个节点级租约、跨 TTL 时续租同一租约、完成/失败/超时/取消后立即释放，Text Analysis 内部大模型分片不派生平台租约。
- [x] 4.5 保持 PPT Slice 从后台任务受理到终态持久化的一个长租约，补齐上下文绑定、周期续租失败处理和仅在终态事务后释放的测试。
- [x] 4.6 扩充 dispatcher/executor/control client 测试，覆盖先取后绑时序、空领取释放、HTTP 跨 TTL 自动续租、续租失败、取消/异常/硬超时释放、调用方停止续租后 TTL 回收、容量等待状态和不重复计费。

## 5. PPT OCR 与关键词工作项租约

- [x] 5.1 改造 `PptTextPipeline` 的 OCR 执行签名，不再接收整节点固定 `instance_url`，而是在每个 `ppt_image_id` 协程内申请一个带任务/节点/图片上下文的 `ocr` 租约；单图 HTTP 调用跨 TTL 时续租同一个租约。
- [x] 5.2 改造关键词执行签名，使每张 OCR 结果独立申请 `extract_keywords` 租约、使用该租约实例调用 `/v1/extract_keywords`，跨 TTL 时自动续租，并在单项结果持久化后释放。
- [x] 5.3 将工作项容量不足建模为可恢复等待，保留已完成 OCR/关键词结果和 `ppt_image_id` 对应关系，不把单纯容量不足转成节点或课程失败。
- [x] 5.4 保留 `PptWorkLimits` 作为单编排进程的协程扇出上限，增加断言证明它不替代全局租约且 N 个并发图片最多只有 N 个项目租约、没有外层同能力租约。
- [x] 5.5 增加多 OCR/Text Analysis 实例测试，证明同一 PPT 节点的不同图片可以落到不同实例，结果仍按原工作项身份和 PostgreSQL 结构持久化。
- [x] 5.6 增加失败、取消、部分完成和重启恢复测试，确认每个工作项租约释放/过期可恢复，既有 PPT 共享路径、manifest 和关键词结果契约不变。

## 6. Vision Orchestrator 的 VBas 租约

- [x] 6.1 扩展 `CapacityLeaseHttpClient` 和 VBas 批次客户端，在一次学生/教师 HTTP 批次租约中写入课程 `task_id`、批次 `work_id`、流类型和追踪信息，并在有限 HTTP 硬超时内为跨 TTL 的批次自动续租。
- [x] 6.2 确认教师请求的可选头部姿态仍属于同一教师批次租约；学生、教师及 `teacher_head_pose` 能力过滤不得产生独立实例容量池。
- [x] 6.3 为 VBas 批次并发、容量不足、跨 TTL 续租、续租失败、超时/异常释放和上下文内容增加测试，验证批次内多帧只计一个租约且现有学生/教师响应解析不变。

## 7. Online Gateway 租约客户端与单图 OCR

- [x] 7.1 扩展 Online Gateway 容量客户端，使 HTTP 请求可在申请时写入在线工作上下文，并为可能跨 TTL 的同步 HTTP 与 WebSocket 提供后台续租、有限硬超时、正常关闭、上游断连和续租失败清理。
- [x] 7.2 为现有 VBas、FaceRec、ScreenDet 和 ASR Online 路由补充 `source_service/work_type/work_id/trace_id`，不改变已有请求响应和业务错误码。
- [x] 7.3 新增单图 OCR 请求模型：`image` 必填，`image_id` 可选且可生成，`enable_formula` 为严格布尔并默认 `false`；在 Online Gateway 根配置写入并实际执行 `body.max_bytes=75497472`、`base64.max_decoded_bytes=52428800`，确保超限请求在申请租约前被拒绝。
- [x] 7.4 实现 `POST /api/online/ocr/recognize`，申请一个 `ocr` 租约，把单图映射为 `/ocr/prediction` 的单元素 `key/value`，并在 `BusinessResponse.data` 保留 OCR 原始响应语义。
- [x] 7.5 实现并测试参数错误 `40001`、容量不足 `50301`、OCR 调用/格式错误 `50000` 的 HTTP `200` 映射，确认网关和 Control Service 都不为在线请求排队或发布 Kafka。
- [x] 7.6 扩充 Online Gateway 路由和容量客户端测试，覆盖默认/开启公式、自动/显式图片标识、普通 Base64/Data URL、72 MiB 正文边界、50 MiB 解码边界、HTTP 跨 TTL 续租、续租失败、超时/取消释放及现有四类路由回归。

## 8. 八个算子统一配置改造

- [x] 8.1 改造 ASR Online 配置加载和 `app.main`，显式传入完整 `[platform]`，从 `[runtime].require_gpu` 执行设备失败关闭，默认容量为 `10`；增加根/部署/覆盖/非法配置测试且保持一个 WebSocket 会话的现有推理契约。
- [x] 8.2 改造 ASR Offline 配置加载和 `app.main`，显式传入完整 `[platform]`，从 `[runtime].require_gpu` 执行设备失败关闭，默认容量为 `4`；明确保留 `concurrency=5`、内部排队和模型串行锁，并增加两类容量互不覆盖测试。
- [x] 8.3 改造 FaceRec 的 Pydantic 配置和 `app.main`，显式传入完整 `[platform]`，将 GPU 强制检查从 `REQUIRE_GPU` 迁移到 `[runtime].require_gpu`，默认容量为 `128`；保留 `thread.max_workers` 作为 Dlib 进程池大小并增加独立语义测试。
- [x] 8.4 改造 OCR 的 settings 和 `app.main`，显式传入完整 `[platform]`，将 GPU 强制检查迁移到 `[runtime].require_gpu`，默认容量为 `256`；将根配置 `ocr.image_max_bytes` 更新为 `52428800` 并实际执行单图 50 MiB 限制，保留 `ocr.max_concurrency=1`、引擎锁及现有多图算子协议。
- [x] 8.5 改造 ScreenDet 的 settings/application，在现有 `[runtime]` 追加 `require_gpu`、显式传入完整 `[platform]`，默认容量为 `128`；保留 `runtime.max_image_bytes` 和 `max_batch_size` 的原语义并增加独立测试。
- [x] 8.6 改造 PPT Slice 配置模型、任务管理器构造和注册逻辑，显式传入完整 `[platform]` 和 `runtime.require_gpu=false`，使用默认 `10` 同时驱动本地任务上限和平台容量，移除 `task.max_concurrent_tasks` 的读取与冲突测试。
- [x] 8.7 改造 VBas 配置和 `app.main`，显式传入完整 `[platform]`，将 GPU 强制检查迁移到 `[runtime].require_gpu`，默认 `128` 作为学生/教师/头部姿态相关能力的共享平台注册容量；保留 `MaxConcurrentBatches`、`MaxQueueSize` 和模型安全控制。
- [x] 8.8 改造 Text Analysis 配置和 `app.main`，显式传入完整 `[platform]` 和 `runtime.require_gpu=false`，默认 `256` 并仅为 `course_overviews/extract_keywords` 共享注册；保留所有历史路由与内部 LLM 并发字段并增加路由基线测试。
- [x] 8.9 在八个项目的根配置或受版本控制的本地安全模板中加入带中文注释的 `[platform]` 四字段和 `[runtime].require_gpu=false`，同步代码默认值；严格测试布尔、URL、有限正心跳、正整数容量、`CONFIG_PATH` 及 ScreenDet 现有 `[runtime]` 合并，确认不存在同义环境变量覆盖。
- [x] 8.10 扩充公共算子 ops 契约测试，确认八个实例 `/ops/status` 与注册请求均暴露 TOML 容量，注册开关/地址/心跳来自 TOML，`reported_inflight` 继续上报实际已接收请求且本地 admission 只处理排空、不按声明容量拒绝请求。

## 9. 部署配置、镜像与预检

- [x] 9.1 在 `deploy/config/operators/` 的八份受控 TOML 中写入 `registration_enabled=true`、容器 Control URL、心跳 `5`、确认容量和中文注释；六类 GPU profile 设置 `runtime.require_gpu=true`，两类 CPU profile 设置 `false`，OCR 同步设置 `image_max_bytes=52428800`，PPT 删除旧 `max_concurrent_tasks`。
- [x] 9.2 从 `docker-compose.operators.yml` 的全部 24 个算子实例删除 `PLATFORM_REGISTRATION_ENABLED`、`PLATFORM_CONTROL_SERVICE_URL`、`PLATFORM_HEARTBEAT_INTERVAL_SECONDS`、`PLATFORM_DECLARED_CAPACITY`、`REQUIRE_GPU`，并从 18 个 GPU 实例删除 `GPU_PROCESS_NAME`；保留 Token、实例 ID、服务 URL、GPU ID/可见设备、配置挂载、端口和单 worker 约束。
- [x] 9.3 使用 YAML mapping anchors 收敛公共 Token、worker 以及每类算子的 `CONFIG_PATH`、容器端口等重复映射；更新部署合同与静态测试，同时校验 Compose 源文件和 `docker compose config` 展开结果，确保 24 个实例身份、URL、端口、Token、GPU reservation/ID/可见设备完整且唯一。
- [x] 9.4 更新注册预检脚本，从实际挂载 TOML 校验注册开关、Control URL、心跳、正整数容量和 `require_gpu`，再与 24 个实例的实际注册容量、能力共享关系、心跳/模型就绪和 GPU 绑定对账；把非法配置和源/展开配置漂移纳入失败关闭检查。
- [x] 9.5 重建并验证 `operator_registry_client` wheel 被八个算子镜像安装，镜像中不保留五个已迁移环境变量的读取代码；验证六类 GPU 入口脚本在未设置 `GPU_PROCESS_NAME` 时仍使用确认名称，版本/`EXPECTED_GIT_SHA` attestation 仍通过。
- [x] 9.6 更新 Online Gateway 配置/部署文档和 Smoke 清单，纳入单图 OCR、72 MiB 正文与 50 MiB 解码限制；若存在反向代理，验证其请求体上限不小于 72 MiB，但不开放新的算子宿主机端口或改变 2B 外部暴露边界。

## 10. 文档与 Harness

- [x] 10.1 更新八个算子 README，逐一说明根/部署 TOML 的注册开关、地址、心跳、容量、GPU 要求，以及继续留在 Compose 的实例/秘密/容器字段和保留的本地并发字段；配置位置与验证边界属于长期规则时同步对应 `AGENTS.md`。
- [x] 10.2 更新 Control Service、Orchestrator、Vision Orchestrator 和 Online Gateway README，说明活跃租约权威、上下文接口、在线无队列/离线等待和单图 OCR 契约。
- [x] 10.3 核对并修订当前受版本控制的总体设计，使旧 `max(active_leases, reported_inflight)` 口径改为活跃租约调度权威及心跳差异观测；保留已封存的历史实施记录，且按用户边界不编辑、不提交未跟踪的运维可视化初稿。
- [x] 10.4 在 Harness 调整台账和验证矩阵中建立“规格场景 -> 代码 -> 自动测试 -> 运行证据”映射，明确每项实际达到的验证层级和未执行原因。
- [x] 10.5 更新 A 服务对接指南，记录 `/api/online/ocr/recognize` 请求/响应、`enable_formula=false` 默认值、`50301` 直接返回 A 服务且不代表 Control Service 排队。

## 11. 平台静态、单元与集成验证

- [x] 11.1 运行平台和四个根服务的格式/编译/导入检查，验证 `control_service.app`、`orchestrator_service.app`、`vision_orchestrator_service.app`、`online_gateway_service.app` 不发生顶层 `app` 冲突。
- [x] 11.2 运行公共注册包、八算子配置加载、Control Service、dispatcher/executor、PPT 文本管道、VBas 客户端和 Online Gateway 的定向测试，并记录命令、通过数与代码 revision。
- [x] 11.3 运行真实 PostgreSQL/Redis 集成测试，确认本变更不新增租约明细表或高频 SQL，Redis 原子容量/TTL/绑定/查询在并发下满足规格。
- [x] 11.4 启动四个平台服务并检查 `/health`、readiness、后台循环和优雅关闭；通过真实 Control Service API 完成租约申请、绑定、续租、查询和释放，并验证有限 HTTP 超时与短租约 TTL 相互独立。
- [x] 11.5 运行现有跨服务回归套件，确认 A 面字段/业务码、四条离线管道、在线 VBas/FaceRec/ScreenDet/ASR、PPT shared-path 和结果持久化没有回归。

## 12. 八个算子本地运行与真实推理验证

- [x] 12.1 在 `asr` 环境对 ASR Online 执行 `compileall`、`from app.main import app`、项目测试、8084 启动/健康检查和 `test/chinEng-16k.wav` 实时会话，确认一个会话一个租约语义。
- [x] 12.2 在 `asr` 环境对 ASR Offline 执行 `compileall`、导入、项目测试、8083 启动/健康检查和 `test_wav/chinEng-16k.wav` 真实转写，并并发验证平台注册 `4` 不破坏本地最多接收 `5` 后排队的独立部署逻辑。
- [x] 12.3 在 `facerecapi` 环境对 FaceRec 执行 `compileall`、导入、项目测试、8003 启动/就绪和 `tests/data/常泽宇.png` 真实识别，确认路由与 Dlib `max_workers` 不变。
- [x] 12.4 在 `ocr-v6` 环境对 OCR 执行 `compileall`、导入、项目测试、8866 启动/版本检查和 `scripts/smoke_test.py`/真实图片推理，确认注册 `256` 时引擎仍按本地 `1` 串行，并验证 `image_max_bytes=52428800` 的边界行为。
- [x] 12.5 在 `screen_det` 环境对 ScreenDet 执行 `compileall`、导入、项目测试、8880 启动/健康检查和现有 `detect_all` 单图 fixture，确认 `max_batch_size` 与平台 `128` 独立。
- [x] 12.6 在 `ppt_slice` 环境对 PPT Slice 执行 `compileall`、导入、项目测试、9001 启动/版本检查和文档化视频任务/终态回调，确认本地与注册容量都为 `10`、共享目录和原子 manifest 不变。
- [x] 12.7 在 `jy-tias` 环境对 VBas 执行 `compileall`、导入、项目测试、8981 启动/健康检查，并直接调用学生和教师真实图片接口，确认共享注册 `128` 不破坏本地批次保护和可选头部姿态。
- [x] 12.8 在 `ai_report` 环境对 Text Analysis 执行 `compileall`、导入、项目测试、8000 启动和完整路由基线对比，并分别真实调用 `/v1/extract_keywords` 与 `/v1/course_overviews`，确认共享注册 `256` 且内部 LLM 并发不派生租约。
- [ ] 12.9 对八个项目分别以根配置或受版本控制的本地安全模板验证“不主动注册且不强制 GPU”，再以受控部署 TOML/契约环境验证注册开关、Control URL、心跳、容量和 GPU 要求生效；同时注入已删除的旧环境变量，确认其不能覆盖 TOML，并将最终 SHA 的 16 进程结果原子写入 release `preflight/operator-config-authority.json`，同 SHA 续跑只复用经严格校验的已有证据。

## 13. 跨服务容量与在线 OCR 验收

- [x] 13.1 用契约 OCR 替身贯通 Online Gateway -> Control Service -> OCR，覆盖省略/开启公式、图片标识、72 MiB 正文、50 MiB 解码、原始 OCR 响应、`40001/50301/50000` 和请求结束立即释放容量。
- [x] 13.2 用真实 Redis 并发发送在线单图 OCR 与离线 PPT OCR，证明两类请求无预留地共享实例容量、允许偏向 `ocr-gpu0`、不会超过总租约上限且离线无容量只等待。
- [x] 13.3 贯通多图片 `PPT_SLICE -> PPT_OCR -> PPT_KEYWORDS`，查询每个实例活跃租约并证明每张图片独立归属、无节点级重复租约、结果身份和数据库结构不变。
- [x] 13.4 贯通离线 ASR -> `course_overviews` 和 Vision Orchestrator -> VBas，核对普通节点/批次各一个租约、上下文中的任务 ID 正确、长任务续租与终态释放可靠。
- [x] 13.5 对在线 VBas、FaceRec、ScreenDet、OCR 和 ASR 发起并发容量耗尽测试，确认上游直接收到 `50301`、Control Service/网关无排队；同时确认算子内部等待仍计活跃租约。
- [x] 13.6 制造 `reported_inflight` 高于和低于活跃租约的心跳时差，确认运维查询/指标显示差异但不会阻塞已释放槽位或伪造任务。
- [x] 13.7 对 Orchestrator、Vision Orchestrator 和 Online Gateway 的同步 HTTP 调用执行“请求时间超过单次 TTL”测试，确认持续续租同一个租约；再覆盖续租失败、请求超时、取消和调用方停止续租后的 TTL 自动回收。

## 14. 里程碑 2B 部署门禁与交付

- [ ] 14.1 在 clean clone 环境按 Harness 准备 `.venv` 和依赖，执行静态、单元、真实 Redis/PostgreSQL、服务运行、算子契约六层验证并原子记录 revision 与环境证据。
- [x] 14.2 对八种 profile 及全 24 实例执行镜像 preflight、启动、注册核验和 operator Smoke，确认每个实例从实际挂载 TOML 取得注册开关、Control URL、心跳、容量和 GPU 要求，Compose 展开后的实例身份、GPU 标签、单 worker、端口绑定和模型 revision 正确。
- [ ] 14.3 运行里程碑 2B 的 PPT、ASR、教师/学生视觉及在线链路回归，确认新增 OCR 不破坏当前 `ppt-ocr-关键字` 泳道和既有 A 服务接口。
- [ ] 14.4 执行容量并发/释放稳定性观察，确认短 OCR/关键词租约可快速复用、跨 TTL 的同步 HTTP 与长 WebSocket/PPT 租约持续续期、无 Redis 孤立租约和无新增 PostgreSQL 写放大。
- [ ] 14.5 演练排空、服务重启和回滚顺序，确认新格式租约在回滚前已释放/过期，旧部署容量配置能成套恢复且不执行破坏性清理。
- [ ] 14.6 汇总 Harness 报告、路由兼容清单、八算子真实推理结果和剩余风险；只有所有强制门禁有可复现证据时才完成本 OpenSpec 实施任务。
- [ ] 14.7 在 `192.168.29.11` 构建前记录旧平台/算子镜像引用、精确 ID、revision、大小和容器引用；最终 SHA 新镜像完成构建、revision 校验、容器替换、基础健康、24 实例注册和算子 Smoke 后，只删除无容器引用且由本工作区 Compose 槽位和旧 release revision 共同证明身份的旧平台/算子镜像，记录删除清单与释放空间，并以自动测试禁止强制删除、宽泛 prune 及基础镜像/基础设施/原业务镜像/模型/数据/证据越界清理。

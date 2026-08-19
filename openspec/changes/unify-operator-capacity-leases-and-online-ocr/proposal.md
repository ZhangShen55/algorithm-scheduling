## 为什么

八个算子目前使用不同字段或部署环境变量声明容量，平台租约又把短周期租约与滞后的心跳处理中数量混合作为调度占用，导致“可分发容量”“算子本地执行并发”和“当前任务归属”三类事实无法准确区分。与此同时，在线网关缺少单图 OCR 入口，PPT 的逐图 OCR/关键词调用也没有按真实工作单元独立占用容量，因此需要一次跨项目收敛，在不破坏现有 PPT、OCR、关键词和算子接口的前提下建立一致契约。

## 变更内容

- 八个算子统一从 TOML 的 `[platform]` 读取 `registration_enabled`、`control_service_url`、`heartbeat_interval_seconds` 和 `max_concurrent_requests`，其中容量在注册时映射为 `declared_capacity`；容量只允许正整数，其他字段也采用严格类型和启动期校验。
- 八个算子的 GPU 强制检查统一从 TOML 的 `[runtime].require_gpu` 读取；项目根配置或受版本控制的本地安全模板使用本地安全默认值，里程碑 2B 的 `deploy/config/operators/*.toml` 使用容器注册地址并为六类 GPU 算子显式启用 GPU 检查。
- 固定每实例默认值：ASR Online `10`、ASR Offline `4`、FaceRec `128`、OCR `256`、ScreenDet `128`、PPT Slice `10`、VBas `128`、Text Analysis `256`。
- 明确同一实例的全部能力共享一个总容量池：VBas 的学生、教师及头部姿态能力共享 `128`，Text Analysis 的 `course_overviews` 与 `extract_keywords` 共享 `256`，不拆分能力配额。
- 明确平台容量与算子内部保护机制相互独立：保留 ASR Offline 的本地排队和串行模型锁、OCR 的单路引擎锁、FaceRec 的线程池、ScreenDet 的单请求批量限制及其他模型安全约束；PPT Slice 改用统一字段同时作为平台声明容量和本地任务上限。
- Control Service 以 Redis 活跃租约作为平台分发占用的权威事实；`reported_inflight` 仅用于观测和差异告警，不再阻塞已释放的短请求容量。
- 扩展租约记录，保存 `work_context`、`acquired_at` 和 `expires_at`，支持在先取租约后领取节点的流程中补绑上下文，并可按实例查询当前活跃租约及对应任务、节点、工作项和追踪标识。
- 编排端改为一个真实工作单元占用一个租约：单次离线 ASR、单个 PPT Slice 后台任务、单次课程脑图请求、每张图片的 OCR、每张图片的关键词提取，以及每次 VBas 推理调用分别申请、续期和释放租约；Text Analysis 内部对大模型的多路请求不额外计租约。HTTP 请求使用有限硬超时，租约 TTL 与请求超时相互独立，所有可能超过单次 TTL 的同步 HTTP 调用均周期续租。
- Online Gateway 新增单图 OCR 同步接口，请求中的 `enable_formula` 可省略且默认 `false`；在线与离线 OCR 请求平等竞争同一实例池，不设置固定实例偏好，也不进入 Kafka 或离线任务队列。
- Online Gateway 在申请算子租约前执行统一图片入口限制：`body.max_bytes=75497472`（72 MiB），`base64.max_decoded_bytes=52428800`（50 MiB）；OCR 算子及受控部署配置同步使用 `ocr.image_max_bytes=52428800`（50 MiB）。
- **BREAKING（部署配置）**：八个算子的部署模板不再使用 `PLATFORM_REGISTRATION_ENABLED`、`PLATFORM_CONTROL_SERVICE_URL`、`PLATFORM_HEARTBEAT_INTERVAL_SECONDS`、`PLATFORM_DECLARED_CAPACITY` 和 `REQUIRE_GPU` 覆盖 TOML；GPU 镜像依赖各自入口脚本的稳定默认进程名并从 Compose 删除 `GPU_PROCESS_NAME`。实例 ID、服务 URL、注册 Token、物理 GPU ID、NVIDIA 可见设备、配置路径、端口、worker、镜像、挂载、网络和资源限制仍由 Compose 管理。
- 使用 Compose YAML anchors 收敛 Token、worker、配置路径和容器端口等重复映射，但渲染后的 24 个实例仍必须具有独立且可核验的实例身份、服务 URL 和 GPU 绑定。
- 保持所有既有 HTTP/WebSocket 路径、方法、请求响应字段、默认端口、PPT 共享路径及 OCR/关键词持久化流程不变。

## 能力范围

### 新增能力

- `unified-operator-capacity`: 定义八个算子的统一平台/GPU配置归属、容量默认值与校验、注册映射、共享池语义、Compose 实例边界及本地并发边界。
- `attributed-capacity-leases`: 定义 Control Service 的权威租约占用、工作上下文、活跃租约查询，以及在线和离线编排按真实工作单元使用租约的规则。
- `online-ocr-routing`: 定义 Online Gateway 的单图 OCR 契约、公式开关默认值、实例选择、容量不足和上游失败行为。

### 调整能力

无。相关原始能力仍位于尚未归档的 `build-algorithm-scheduling-platform` 变更中，尚未同步至主规格目录；本变更用独立的新能力补充并收紧其容量与在线 OCR 契约。

## 影响范围

- 八个算子项目：`asr_online`、`asr_offline`、`facerec`、`ocr`、`screen_det`、`ppt_slice`、`vbas`、`text_analysis` 的配置模型、注册启动逻辑、配置样例、测试、README 和必要的本地容量衔接。
- 公共包：`operator_registry_client`、`platform_common`、`platform_contracts` 的显式 TOML 注册参数、容量校验、注册字段、Redis 租约结构、客户端协议和测试。
- 平台服务：`control_service` 的租约 API 与查询接口，`orchestrator_service` 和 `vision_orchestrator_service` 的租约粒度/上下文绑定，`online_gateway_service` 的 OCR 路由、请求校验、错误映射和指标。
- 部署与验收：八算子 Compose/TOML 模板、已迁移平台/GPU环境变量清理、YAML anchors、展开配置校验、跨服务测试、真实 Redis 并发测试、各算子规定环境下的编译/导入/启动/路由/真实推理，以及 Harness 证据和相关设计文档。
- 里程碑 2B 在 `192.168.29.11` 以新 Git SHA 重新构建平台与八算子镜像；新镜像完成 revision 校验、容器替换及基础健康/注册/Smoke 后，按精确镜像 ID 删除服务器上的旧平台/算子版本以回收空间，并记录删除证据。
- 不新增 PostgreSQL 高频租约明细写入；活跃工作明细只保存在 Redis，既有审计与任务事实继续按当前职责落 PostgreSQL。

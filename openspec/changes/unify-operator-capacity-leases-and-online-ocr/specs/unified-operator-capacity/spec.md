## ADDED Requirements

### Requirement: 八个算子使用统一平台配置段
ASR Online、ASR Offline、FaceRec、OCR、ScreenDet、PPT Slice、VBas 和 Text Analysis SHALL 从所选 TOML 的 `[platform]` 读取 `registration_enabled`、`control_service_url`、`heartbeat_interval_seconds` 和 `max_concurrent_requests`，并 SHALL 在启用注册时把容量原样映射为 `declared_capacity`。

#### Scenario: 使用项目本地安全配置
- **WHEN** 八个算子分别使用仓库提交的根配置或受版本控制的本地安全模板启动
- **THEN** `registration_enabled` SHALL 为 `false`、`control_service_url` SHALL 为空、`heartbeat_interval_seconds` SHALL 为 `5`，算子 SHALL 不发起注册，且运行时容量 SHALL 依次为 ASR Online `10`、ASR Offline `4`、FaceRec `128`、OCR `256`、ScreenDet `128`、PPT Slice `10`、VBas `128` 和 Text Analysis `256`

#### Scenario: 使用里程碑 2B 部署配置
- **WHEN** 八个算子通过 `CONFIG_PATH` 使用 `deploy/config/operators/` 中对应 TOML 启动
- **THEN** `registration_enabled` SHALL 为 `true`、`control_service_url` SHALL 为 `http://control-service:18100`、`heartbeat_interval_seconds` SHALL 为 `5`，并 SHALL 注册各自确认容量

#### Scenario: 使用 CONFIG_PATH 覆盖配置文件
- **WHEN** 算子通过 `CONFIG_PATH` 指向包含合法 `[platform]` 的另一份 TOML
- **THEN** 算子 SHALL 使用该文件中的注册开关、地址、心跳和容量且不依赖当前工作目录

### Requirement: 平台配置具有严格校验语义
`platform.registration_enabled` SHALL 为严格布尔值；启用注册时 `platform.control_service_url` SHALL 为非空 HTTP(S) URL；`platform.heartbeat_interval_seconds` SHALL 为有限正数；`platform.max_concurrent_requests` 和注册协议中的 `declared_capacity` SHALL 只接受正整数。

#### Scenario: 启用注册但地址无效
- **WHEN** `registration_enabled=true` 但 `control_service_url` 为空或不是合法 HTTP(S) URL
- **THEN** 算子 SHALL 在开始接收业务请求前失败并给出明确的中文配置错误

#### Scenario: 心跳或布尔字段非法
- **WHEN** 心跳为零、负数、非有限值或错误类型，或者注册开关不是严格布尔值
- **THEN** 算子 SHALL 在开始接收业务请求前失败

### Requirement: 容量值具有统一校验语义
`platform.max_concurrent_requests` 和注册协议中的 `declared_capacity` SHALL 只接受正整数。

#### Scenario: 配置正整数
- **WHEN** `platform.max_concurrent_requests` 是正整数
- **THEN** 算子 SHALL 成功启动并以该正整数注册

#### Scenario: 配置非法值
- **WHEN** 该字段为 `0`、负数、布尔值、浮点数或字符串
- **THEN** 算子 SHALL 在开始接收业务请求前失败并给出明确的中文配置错误

### Requirement: GPU 强制检查由部署 TOML 控制
八个算子 SHALL 从所选 TOML 的 `[runtime].require_gpu` 读取严格布尔值；项目根配置 SHALL 默认为 `false`，里程碑 2B 部署配置中 ASR Online、ASR Offline、FaceRec、OCR、ScreenDet 和 VBas SHALL 为 `true`，PPT Slice 和 Text Analysis SHALL 为 `false`。

#### Scenario: GPU 部署未检测到 CUDA
- **WHEN** 六类 GPU 算子使用 `runtime.require_gpu=true` 启动但无法取得受支持的 CUDA 设备
- **THEN** 算子 SHALL 在模型就绪和平台注册前失败关闭

#### Scenario: CPU 算子部署
- **WHEN** PPT Slice 或 Text Analysis 使用 `runtime.require_gpu=false` 启动
- **THEN** 算子 SHALL NOT 因没有 CUDA 设备而失败

### Requirement: 同一实例的能力共享总容量池
一个 `instance_id` 声明的所有能力 SHALL 共享一个 `declared_capacity` 和同一组活跃租约，平台 SHALL NOT 为每个能力重复计算一份实例容量。

#### Scenario: VBas 多能力同时被调用
- **WHEN** 同一个 VBas 实例同时承接学生行为、教师行为或教师头部姿态相关调用
- **THEN** 所有调用 SHALL 共同占用该实例总计 `128` 的池且 SHALL NOT 为每种能力各提供 `128`

#### Scenario: Text Analysis 两个能力同时被调用
- **WHEN** `course_overviews` 和 `extract_keywords` 同时选择同一个 Text Analysis 实例
- **THEN** 两类租约 SHALL 共同占用该实例总计 `256` 的池

### Requirement: 平台容量与算子本地执行约束分离
统一容量字段 SHALL 只控制平台可分发工作单元总数，除 PPT Slice 外 SHALL NOT 覆盖算子为独立部署、批量边界或模型安全保留的本地队列、锁、信号量、线程池和批量限制。

#### Scenario: ASR Offline 保留本地排队
- **WHEN** ASR Offline 把平台容量注册为 `4`
- **THEN** 现有 `concurrency=5`、模型串行运行和超过本地接收数后的排队行为 SHALL 保持不变

#### Scenario: OCR 保留引擎串行保护
- **WHEN** OCR 把平台容量注册为 `256`
- **THEN** `ocr.max_concurrency=1` 和引擎锁 SHALL 继续限制本地实际推理并发，且平台字段 SHALL NOT 被改写为 `1`

#### Scenario: FaceRec 与 ScreenDet 保留不同语义字段
- **WHEN** FaceRec 和 ScreenDet 使用统一平台容量
- **THEN** FaceRec 的 `thread.max_workers` SHALL 只控制 Dlib 进程池，ScreenDet 的 `max_batch_size` SHALL 只控制单请求批量大小

#### Scenario: PPT Slice 统一同义限制
- **WHEN** PPT Slice 读取 `platform.max_concurrent_requests=10`
- **THEN** 它 SHALL 同时以 `10` 注册平台容量并将本地后台任务上限设置为 `10`，且 SHALL NOT 再从另一个同义字段读取不同值

### Requirement: 类型级平台与 GPU 设置只有一个配置权威
八个算子的受控部署 SHALL 从各自 TOML 读取注册开关、Control Service 地址、心跳、容量和 GPU 强制检查。公共注册运行时、算子代码和部署定义 SHALL NOT 再读取或设置 `PLATFORM_REGISTRATION_ENABLED`、`PLATFORM_CONTROL_SERVICE_URL`、`PLATFORM_HEARTBEAT_INTERVAL_SECONDS`、`PLATFORM_DECLARED_CAPACITY` 或 `REQUIRE_GPU`。

#### Scenario: 检查受控部署配置
- **WHEN** 校验八算子 Compose、部署 TOML 和启动脚本
- **THEN** 每个实例的五类设置 SHALL 能追溯到对应 TOML 且 SHALL NOT 存在同义环境变量覆盖

### Requirement: 实例级和容器启动参数继续由 Compose 管理
受控部署 SHALL 继续由 Compose 提供 `PLATFORM_OPERATOR_REGISTRY_TOKEN`、`PLATFORM_INSTANCE_ID`、`PLATFORM_SERVICE_URL`、GPU 实例的 `PLATFORM_GPU_ID` 与 `NVIDIA_VISIBLE_DEVICES`、`CONFIG_PATH`、容器端口和 `UVICORN_WORKERS=1`，并 SHALL 继续由 Compose 管理镜像、端口映射、挂载、网络、GPU reservation 和 CPU/内存限制。

#### Scenario: 同类算子的三个副本
- **WHEN** 同一份算子部署 TOML 被 gpu0、gpu1、gpu2 三个 Compose service 挂载
- **THEN** 三个副本 SHALL 共享类型级平台注册设置，但 SHALL 分别具有唯一 `instance_id`、容器网络 `service_url` 和物理 GPU 绑定

#### Scenario: 使用 YAML anchors 收敛重复配置
- **WHEN** Compose 源文件用 YAML mapping anchors 复用 Token、worker、配置路径或容器端口
- **THEN** `docker compose config` 展开后的 24 个实例 SHALL 保留完整且正确的实例身份、服务 URL、端口、Token 和 GPU 绑定

### Requirement: GPU 进程名使用镜像稳定默认值
里程碑 2B Compose SHALL NOT 再设置 `GPU_PROCESS_NAME`；六类 GPU 镜像入口脚本 SHALL 分别使用已确认的算子进程名默认值，并 SHALL 保持 `nvidia-smi`/进程证据可归属。

#### Scenario: GPU 算子不设置进程名环境变量
- **WHEN** 六类 GPU 算子在 Compose 中未设置 `GPU_PROCESS_NAME` 而完成真实推理
- **THEN** GPU 进程证据 SHALL 仍显示对应算子默认进程名并能关联到正确容器、实例和物理 GPU

### Requirement: 既有算子契约保持兼容
容量统一改造 SHALL 保留八个算子的现有 HTTP/WebSocket 路径、方法、请求字段、响应字段、默认端口、模型路径和已批准的 PPT 共享路径契约。

#### Scenario: 对比改造前后路由
- **WHEN** 在每个算子完成配置和注册改造后导出路由清单
- **THEN** 除既有运维路由的容量值语义外，业务路径和方法 SHALL 与改造前基线一致

### Requirement: 里程碑 2B 精确清理旧平台和算子镜像
`192.168.29.11` SHALL 使用最终 Git SHA 重新构建平台与八算子镜像，并 SHALL 只在新镜像完成 revision 校验、容器替换、基础健康、24 实例注册和算子 Smoke 后，按预先记录的精确镜像引用或镜像 ID 删除不再被容器引用的旧平台/算子镜像。

#### Scenario: 平台替换前迁移并对账任务库
- **WHEN** 最终发布准备替换四个平台容器
- **THEN** Canonical SHALL 先幂等应用并核验当前 `0006` 前向迁移，且后续 runtime preflight 的 PostgreSQL 必需列集合 SHALL 与 Control Service readiness 的权威列集合完全一致

#### Scenario: 前驱在算子账本初始化前中断
- **WHEN** 立即前驱没有完整 `baseline/new`，其 maintenance provenance 到达一个合法 direct maintenance release，且该 release 具有当前 UID 所有、单链接、`0400` 的同 tag predecessor marker
- **THEN** 只读 resolver SHALL 可沿 marker 查找更早的完整算子账本，但 SHALL NOT 改写历史 marker/provenance，并 SHALL 仅在 `current - resolved baseline == resolved new` 精确成立后继承账本

#### Scenario: 已恢复的 direct 前驱尚无算子账本
- **WHEN** 候选 release 的 direct maintenance 已通过唯一 `0400` 终态 audit 完成 restore、没有完整 `baseline/new`，且具有合法的同 tag predecessor marker
- **THEN** 只读 resolver SHALL 在重新校验 snapshot、终态 audit、当前恢复事实和 marker 后沿 marker 查找更早的完整算子账本，且 SHALL NOT 把 completed maintenance 本身当作算子账本

#### Scenario: 新版本通过替换门禁
- **WHEN** 新平台与算子镜像通过构建、revision、健康、注册和 Smoke，且旧镜像不再被任何运行中、暂停或停止容器引用
- **THEN** 部署流程 SHALL 只对能够由本工作区 Compose 槽位和旧 release revision 共同证明身份的旧版本按精确镜像 ID 删除、记录删除对象和释放空间，且 SHALL NOT 删除基础设施/基础镜像、服务器原有业务镜像、模型资产、数据卷、课程结果或历史 Harness 证据

#### Scenario: 新版本验证失败
- **WHEN** 新镜像构建、revision、容器健康、注册或 Smoke 任一步失败
- **THEN** 部署流程 SHALL 保留全部旧镜像且 SHALL NOT 执行旧版本清理

#### Scenario: 替换后的后续门禁失败
- **WHEN** 本轮算子容器已按 new ledger 创建，但业务、容量、报告或镜像清理前置门禁失败
- **THEN** Canonical SHALL 保留原退出码，在完整验证 baseline/new 账本及全部容器身份后，只停止 new ledger 中的精确容器并恢复已授权的原业务；用于身份核验的权威 Compose allowlist SHALL 保留到恢复结束，身份不可证明时 SHALL 失败关闭且 SHALL NOT 执行宽泛停止

#### Scenario: 旧镜像仍被容器引用
- **WHEN** 候选旧镜像仍被运行中、暂停或停止容器引用
- **THEN** 清理流程 SHALL 报告并跳过该镜像，且 SHALL NOT 使用强制删除、宽泛 prune 或删除 Docker 数据目录绕过引用保护

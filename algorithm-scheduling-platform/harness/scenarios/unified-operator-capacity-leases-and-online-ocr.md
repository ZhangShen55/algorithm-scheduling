# 统一算子配置、容量租约、在线 OCR 与镜像清理验证场景

## 当前状态

本场景对应 OpenSpec `unify-operator-capacity-leases-and-online-ocr`。2026-08-19 的规划基线已经
通过严格 OpenSpec 校验，proposal、design、三份规格和 tasks 均完整；业务代码、部署配置和
运行证据尚未实施，OpenSpec 的 88 项任务保持未勾选，本场景结论为“待验证”。

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
- 当前仅建立验收合同和证据目录规范；本文中的命令是实施后必须执行的门禁，不代表已经通过。

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

| 规格能力 | OpenSpec 任务 | 最低证据 | 当前结论 |
| --- | --- | --- | --- |
| `unified-operator-capacity` | 1、8、9、12、14 | 根/部署 TOML、Compose 源/展开配置、注册契约、GPU 默认进程名、八算子真实推理、24 实例与精确镜像清理 | 待验证 |
| `attributed-capacity-leases` | 2、3、4、5、6、11、13、14 | 真实 Redis 并发、Control API、跨 TTL 时序、PPT/ASR/VBas 跨服务链路 | 待验证 |
| `online-ocr-routing` | 7、9、11、13、14 | 网关契约替身、72/50 MiB 边界、真实 OCR、在线/离线共享池 | 待验证 |
| 兼容与交付 | 10、11、12、14 | 路由基线、文档、Harness、全回归和里程碑 2B 不可变证据 | 待验证 |

任何矩阵行在只有静态代码、模拟成功响应或健康检查时都不得改为“符合”。真实 Redis、服务运行、
算子契约和三卡部署证据必须分别注明层级，跳过项不能计为通过。

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

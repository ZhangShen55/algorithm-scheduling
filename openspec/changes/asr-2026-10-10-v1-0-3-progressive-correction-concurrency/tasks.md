## 1. 配置与运行边界

- [x] 1.1 在 `asr_online/config.toml` 和 `Settings` 中增加中间纠正开关、稳定新增字符阈值、音频时长阈值、冷却时间、correction worker 数、队列容量和任务超时配置，并为非法范围增加启动校验测试
- [x] 1.2 保持默认 Uvicorn worker 为 1，明确 correction worker 与进程 worker 的区别，并在 README 记录配置语义、显存影响和关闭中间纠正的回滚方式
- [x] 1.3 为 v1.0.3 增加在线 ASR 和 VAD 模型级受控调用入口，确保锁不跨 WebSocket 发送或 SenseVoice 等待持有，并记录排队/执行耗时

## 2. SenseVoice 纠正调度器

- [x] 2.1 新增 correction task/result 数据结构，包含连接标识、`segment_key`、revision、样本边界、任务类型和 PCM 快照，不包含可持久化的完整文本或音频日志
- [x] 2.2 实现有界 final/intermediate 优先队列、每会话一个在途任务和 latest-only pending 快照合并
- [x] 2.3 实现每个 worker 独占 SenseVoice 模型实例的加载、推理和结果回传，禁止多个线程共享调用同一实例
- [x] 2.4 将 worker 池接入 FastAPI lifespan，完成启动就绪、非法配置、模型加载失败、CUDA OOM、停止接单、待处理任务取消和服务关闭资源释放
- [x] 2.5 增加队列深度、在途数、排队/推理耗时、stale 丢弃、队列满、超时和降级的结构化指标日志，确认日志不含完整 ASR 文本或 PCM

## 3. v1.0.3 渐进纠正

- [x] 3.1 将 v1.0.3 从共用终端纠正调用中拆出明确的渐进 revision 状态，同时保持 v1.0.1/v1.0.2 路径和行为不变
- [x] 3.2 实现 VAD 候选 end 触发，以及默认稳定新增 8 字、累计新增音频 4800 ms、冷却 2000 ms 的长语音补充触发
- [x] 3.3 实现连接、`segment_key`、revision 和样本边界校验，确保旧结果、跨 key 结果和断开后的结果不发送
- [x] 3.4 通过会话事件循环顺序发送同 key 的 `finished=false` 中间纠正，文本无变化时去重，并保持 `bg/ed` 单调
- [x] 3.5 在最终边界与已纠正快照一致时复用结果；边界已扩展时使用最新快照；final 超时或失败时有界降级并仍只发送一次 `finished=true`
- [x] 3.6 在连接断开、VAD 候选合并、20.16 秒硬切段和发送失败路径清除 pending、PCM 引用和会话状态，防止任务或缓存泄漏

## 4. 自动化测试与协议回归

- [x] 4.1 为 VAD 候选 end、8 字触发、4.8 秒触发、2 秒冷却、相同边界去重和禁用中间纠正增加确定性单元测试
- [x] 4.2 为每会话单在途、latest-only、final 优先、队列满、任务超时、加载失败和关闭清理增加调度器测试
- [x] 4.3 为 revision 乱序、旧 key、连接断开、边界复用和 final 降级增加协议测试，验证 worker 不直接发送 WebSocket
- [x] 4.4 回归 v1.0.1/v1.0.2/v1.0.3 路由和 `key/text/finished/bg/ed` 字段类型，验证旧接口没有新增中间 SenseVoice 行为
- [x] 4.5 在 Mac `asr` 环境执行 `compileall`、应用导入、全部单元测试、`pip check` 和短音频真实推理，保存检查结果

## 5. 并发压测工具

- [x] 5.1 新增可配置 correction worker 和并发会话矩阵的远端压测入口，所有客户端在 `server12` 本机连接 `127.0.0.1:8084`
- [x] 5.2 实现每连接 0.48 秒真实节奏发送、0 至 480 ms 启动错开、计划/实际发送时间和响应到达时间采集
- [x] 5.3 实现首响应 10 秒、活跃连接 15 秒无响应、积压连续增长、CUDA OOM、进程退出和单点硬超时 watchdog，故障时快速保存诊断并清理
- [x] 5.4 实现 CPU、RSS、GPU 显存、GPU 利用率、队列、在途任务和 WebSocket 关闭码采样，不在报告或日志保存完整识别文本
- [x] 5.5 实现 partial/中间纠正/finished p50/p95/p99、RTF、端到端 lag、发送完整率、时间戳与协议异常、stale 和降级统计
- [x] 5.6 实现按统一阈值判定“实时通过”、矩阵汇总、边界复验选择和中文 Markdown 报告生成

## 6. server12 正确性与容量验证

- [x] 6.1 提交并推送实现，使用 `ssh server12` 同步到 `/root/workspace/asr_online`，核对 GPU 配置、模型路径和代码提交一致
- [x] 6.2 在 worker=1、并发=1 下完整处理 47 分钟 `教师1_16k.wav`，保存完整 JSONL，并与变更前 v1.0.3 C 结果比较纠正位置、finished、时间戳和协议异常
- [x] 6.3 依次以 correction worker=`1/2/4/6` 启动单 Uvicorn worker 服务，等待全部模型就绪；加载失败或 OOM 时保存证据且不静默降级
- [x] 6.4 对每个可启动 worker 配置依次执行并发=`1/2/4/8/16/32/64` 的 10 分钟真实节奏测试，保证 28 个矩阵点都有通过、失败或不可用状态
- [x] 6.5 对每个 worker 配置的最大通过并发和首个失败并发各复验一次，记录波动和瓶颈阶段
- [x] 6.6 使用最终推荐 worker/并发配置再完整处理一次 `教师1_16k.wav`，验证长时运行无缓存增长、卡死、协议或时间轴回退

## 7. 报告、清理与交付

- [x] 7.1 在本 change 生成 `benchmark-report.md`，列出 28 点矩阵、p50/p95/p99、RTF、积压、资源、最大可持续并发、首个失败边界和失败原因
- [x] 7.2 在报告中比较不同 correction worker 的纠正延迟与吞吐收益，明确在线 ASR/VAD、SenseVoice、GPU 显存或队列中的实际瓶颈，并给出生产推荐配置
- [x] 7.3 核对原始 JSONL、监控和服务日志仅位于 `asr_online/test/` 忽略目录，报告和必要脚本纳入版本控制
- [x] 7.4 全部测试或异常退出后关闭 `server12` 的 8084 服务，确认端口、Uvicorn/压测后台进程和本次 GPU 计算进程均已释放
- [x] 7.5 运行 `git diff --check` 和最终回归测试，规范提交并推送代码、OpenSpec 状态和容量报告

# Vision Orchestrator Service

本服务负责离线视觉编排策略、VBas 调用、自适应抽帧、行为聚合和证据筛选。

本服务属于当前七算子拓扑，只依赖 VBas 的帧级推理能力，与 Text Analysis、PPT 关键词和
课程脑图无关；退役范围不改变视觉命令、VBas 租约或视觉结果合同。

在本服务目录安装公共平台包和服务依赖：

```bash
python -m pip install -e ../algorithm-scheduling-platform
python -m pip install -r requirements.txt
```

The canonical entrypoint is:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --workers 1
```

配置按内置默认值、根目录 `config.toml`、`VISION_` 环境变量的顺序加载。
嵌套字段使用双下划线，`CONFIG_PATH` 可以指定其他 TOML 文件。

`/health` 只表示进程存活；`/ready` 会检查视觉命令 Consumer 后台循环，以及按
`[readiness]` 开关启用的 PostgreSQL、Kafka 和 Control Service 依赖。后台循环
退出或必需依赖不可用时返回 HTTP `503`。

服务消费 `algorithm.visual.commands` 中的教师/学生课程级命令。命令只携带
`/data/course/{task_id}` 下的绝对本地视频路径和元数据，不携带媒体字节。服务使用
`ffprobe` 读取时长、`ffmpeg` 抽取 JPEG 帧，再通过 Control Service 租约调用 VBas；
不接入 RTSP，也不重新下载上游视频。抽帧缓存保留在课程临时目录，筛选出的少量证据图
复制到 `/data/result/{task_id}/vision`。`media.max_concurrent_processes` 限制单个服务容器
同时运行的 ffmpeg/ffprobe 进程数，默认 `2`；该字段只保护本地 CPU/内存，不改变扫描点、
VBas 批次或平台注册容量。

视觉命令 Consumer 使用长期在途池：`worker.concurrency` 是课程级命令上限，
`kafka.max_poll_records` 仅是单次预取量。任一课程完成后立即继续 poll 并补位，不等待同一次
poll 中的其他慢课程。执行中的命令和单次 pending 预取都有界；同一 Kafka partition 即使发生
乱序完成，也只提交连续完成的 offset。课程槽位全满时 Consumer 使用不取新消息的 keepalive
poll 维持组成员资格，停止或取消时未完成消息不提交并可重放。

每轮扫描按 `scan.batch_size` 生成稳定的批次计划，生产者与推理消费者之间使用容量为
`scan.batch_prefetch`（默认 `2`）的队列。首批图片准备后立即等待 VBas 离线租约并开始推理，
下一批抽帧可同时进行，尾部不足一批仍会提交。抽帧器只创建固定数量 worker，共享
`media.max_concurrent_processes` 的 FIFO 槽位，因此长课程不会把整节课的数百个时间点一次性
排在其他课程的时长探测和首批抽帧之前。ffmpeg/ffprobe 使用可取消子进程，超时、命令失败和
服务关闭都会回收进程及临时 `.part` 文件。

教师泳道先按可配置粗粒度扫描，命中板书或坐姿后按
`scan.refinement_intervals_seconds` 逐级加密；教师行为区间和学生人数结果写入现有
PostgreSQL 节点结果。`scan.end_frame_margin_seconds` 避开视频末端不可稳定解码的时间点，
但结果时长和行为区间仍使用 ffprobe 返回的真实视频时长。节点成功或业务失败后均发布
`algorithm.visual.events` 终态事件，再提交 Kafka offset。若数据库已进入成功/失败终态但事件
发布中断，重投命令只会按数据库事实补发对应终态事件，不重复执行推理。

每个学生或教师图片批次申请一个共享 VBas 租约，并写入课程、批次、流类型和追踪上下文；
教师请求中的可选头部姿态仍属于同一批次，不额外占用容量。同步批次调用设置有限硬超时，
跨越单次 TTL 时续租同一个租约，并在成功、失败、超时或取消后释放。容量不足属于离线等待，
不会把课程节点标记为最终失败。VBas 尚未注册或暂时满载时，Consumer 保留当前消息且不提交
Kafka offset。租约申请对容量不足、Control 建连/连接超时和 HTTP `502/503/504` 共用
`[lease_renewal].acquire_wait_timeout_seconds` 累计预算，默认 300 秒；退避从
`acquire_retry_interval_seconds` 开始指数增长、带抖动并在 2 秒封顶。重试期间节点保持状态 50，
未取得租约绝不调用 VBas，原 `task_id`、帧集合和稳定 `batch_id` 不变。关闭服务会立即取消等待且
不提交未完成消息。确定性 HTTP 4xx、非法租约响应或预算耗尽会写入非空中文失败原因，并发布视觉
失败终态事件，供 Orchestrator 推进任务和清理媒体。VBas 的学生、教师和头部姿态能力共享同一
实例容量池。
`[lease_renewal]` 允许同一 lease_id 在 TTL 安全窗口内对瞬时读取、连接或受控 5xx 有限重试；
首次 `ReadError` 恢复时原批次继续，最终确认丢失只影响当前批次，不停止其他课程。释放 404
视为幂等成功，瞬时释放结果不确定时记录日志并由 TTL 回收。

Vision 不再使用固定 VBas 批次并发。它按短周期读取 Control 的算子快照，只统计 `ONLINE`、
模型就绪 VBas 实例的 `capacity_pools.offline`，全部课程共享的有效批次并发为这些离线容量之和。
在三实例且每实例 `MaxConcurrentOfflineBatches=1` 时，有效并发就是 `3`；等待批次不会提前
申请租约或调用 VBas。快照只负责本地门控，实例选择和最终并发准入仍以 Control 原子租约为准。

学生全画面、前排和后排分析复用同一时间点的一张本地图片，仅以各自稳定的区域身份调用
VBas，不再重复解码视频。节点查询中的整数状态码保持不变，`reason` 会依次反映校验视频、
探测时长、抽帧 batch 进度、等待 VBas 容量、推理 batch 进度、聚合和持久化阶段。

教师粗扫、加密扫描和学生全画面/区域批次使用由流类型、区域和帧集合稳定派生的 batch ID。
不同帧集合不再复用 `t-0000` 等审计身份，同一逻辑批次的瞬时网络重试保持同一 ID。连接、
读写、远端协议和超时类故障按 `[vbas]` 配置有限重试，每次重新申请租约；HTTP 或业务响应
失败不重试，最终原因始终包含异常类型、实例、批次和尝试次数。

容器镜像安装 `ffmpeg/ffprobe`。本地启动前也必须保证这两个命令可执行，并确保
PostgreSQL 迁移、Kafka 视觉主题和 Control Service 已就绪。VBas 可以后启动，但它注册且有容量前
视觉命令只会保留 offset 等待，不会向前执行。

从工作区根目录构建镜像：

```bash
docker build -f vision_orchestrator_service/docker/Dockerfile \
  -t vision-orchestrator-service .
```
# 日志

运行日志默认写入 `logs/{instance_id}/application.log`，同时输出到 stdout；单文件上限
100 MiB，归档保留 7 日。日志只保留扫描轮次、时间点、租约和聚合摘要，不记录帧图片、
Base64 或完整 VBas 响应。

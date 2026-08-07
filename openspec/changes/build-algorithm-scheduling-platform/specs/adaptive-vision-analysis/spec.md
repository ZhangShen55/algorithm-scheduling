## ADDED Requirements

### Requirement: 视觉编排与 VBas 推理解耦
`vision-orchestrator-service` SHALL 负责视频帧规划、迭代分析、缓存、聚合和结果持久化。VBas SHALL 只执行帧级推理，视觉服务 SHALL 通过感知容量的 HTTP 路由同步调用 VBas。

#### Scenario: 细化分析需要新一轮检测
- **WHEN** 教师粗扫帧显示正在板书
- **THEN** 视觉服务创建更密集的本地抽帧计划并再次调用 VBas，不要求课程 orchestrator 决定单个抽帧点

### Requirement: 长耗时视觉任务使用 Kafka 边界
`orchestrator-service` SHALL 通过 Kafka 向 `vision-orchestrator-service` 发送课程级视觉命令，并通过 Kafka 接收进度/完成事件；视觉服务向 VBas 发起的迭代帧请求 SHALL 使用同步 HTTP。

#### Scenario: 教师行为命令
- **WHEN** 教师行为节点变为就绪
- **THEN** orchestrator 发布任务和本地 T 路视频元数据，视觉服务随后发布进度和完成事件

### Requirement: 可配置的自适应扫描
视觉服务 SHALL 支持可配置的粗扫间隔、按顺序执行的 10/5/2/1 秒等细化间隔、可配置的 VBas 批次大小和并发量、帧结果缓存以及明确的细化限制。

#### Scenario: 二十分钟处发现板书候选
- **WHEN** 粗扫在 20:00 附近检测到板书
- **THEN** 服务向左右扩展以包围状态转换，只对尚未确定的边界和冲突点逐步细化

### Requirement: 行为区间间隙合并
服务 SHALL 将检测点转换为左闭右开的行为区间；当间隙小于或等于该行为配置的 `max_gap_seconds` 时，SHALL 合并相邻区间。初始默认值 SHALL 合并不超过 3 秒的板书间隙和不超过 5 秒的坐姿间隙。

#### Scenario: 第 1-8 秒和第 12-20 秒均为板书
- **WHEN** 归一化产生 `[1,9)` 和 `[12,21)`
- **THEN** 三秒间隙被合并为一个板书区间

### Requirement: 空行为也是已完成的业务结果
当有效分析完成但没有目标行为时，节点 SHALL 保持 `status=60`，返回空区间列表，并且不为该行为创建代表性证据图片。有效帧不足 SHALL 在中文原因中与确认不存在目标行为明确区分；媒体或算子故障 SHALL 使用失败状态。

#### Scenario: 覆盖有效但未检测到板书
- **WHEN** 教师分析完成，拥有足够的有效帧且没有板书区间
- **THEN** 板书区间为 `[]`，节点状态为已完成，并且不生成板书证据图片

#### Scenario: 教师始终未被有效拍摄
- **WHEN** 分析已运行但有效教师帧不足
- **THEN** 结果不虚构站、坐、板书或讲授区间，原因说明有效画面不足

### Requirement: 学生区域指标与兜底值
当提供前排/后排多边形时，视觉服务 SHALL 使用检测总人数作为分母，计算前排和后排稳定人数比例。当任一多边形缺失时，服务 SHALL 针对每个 `task_id` 只生成一次该区域的配置兜底值，持久化并稳定返回，同时提供 `front_region_provided` 和 `back_region_provided` 布尔值。

#### Scenario: 两个区域均未提供
- **WHEN** 学生行为处理未提供 `front_points` 和 `back_point`
- **THEN** 配置的前排和后排兜底值只生成一次，后续每次查询复用，并且两个 provided 标志均为 false

### Requirement: 长期保留的视觉证据
视觉服务 SHALL 只在 `/data/result/{task_id}/vision` 下保留选定的证据图片，包括现有学生抬头、读书、睡觉、玩手机、教师告警类别，以及板书、坐和讲授区间的代表帧。普通抽取帧 SHALL 保持临时性质。

#### Scenario: 行为区间存在代表帧
- **WHEN** 板书区间得到确认
- **THEN** 选定的代表图片保存到长期结果目录，其文件元数据可随结构化区间结果一起查询

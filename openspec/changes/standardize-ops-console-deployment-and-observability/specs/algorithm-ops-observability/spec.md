## ADDED Requirements

### Requirement: 控制台默认读取真实只读数据

控制台 SHALL 默认从配置的 Control Service 和 online-gateway-service 只读接口读取数据，并 SHALL 在页面上区分实时数据、演示数据、加载中和读取失败状态。读取失败时不得把演示快照标识为实时数据。

#### Scenario: 真实接口读取成功
- **WHEN** Control Service 的实例、容量、队列、存储和就绪接口，以及 online-gateway-service 的 `/metrics` 返回成功
- **THEN** 控制台展示响应数据，并在页面上标识“实时数据”和最近采样时间

#### Scenario: 后端不可达
- **WHEN** 任一总览必需接口因网络错误、HTTP 错误或浏览器跨域错误失败
- **THEN** 控制台展示可重试的失败状态、失败接口线索和当前数据来源，不得将演示快照伪装成实时数据

### Requirement: 观测接口映射保持只读边界

控制台 SHALL 只调用以下 GET 接口：`/ops/operator-instances`、`/ops/operator-instances/snapshot`、`/ops/queues`、`/ops/storage`、`/ops/readiness`、`/ops/kafka`、`/ops/course-jobs`、`/ops/course-jobs/{task_id}`、`/ops/operator-instances/{instance_id}/active-leases`、online-gateway-service 的 `/metrics` 和 gpu_metrics_exporter 的 `/gpu`；不得调用注册、心跳、租约写入、排空、恢复上线、重启或 Docker 控制接口。

#### Scenario: 只读网络审计
- **WHEN** 运维人员使用浏览器打开控制台并切换页面、刷新或查询任务
- **THEN** 网络请求方法均为 GET，且请求路径属于规定的只读接口集合

### Requirement: 观测数据按层级刷新

控制台 SHALL 支持自动刷新，默认总览/实例清单/网关/系统状态为 10 秒，实例详情活跃租约为 5 秒；用户关闭自动刷新后不得继续发起定时请求，手动刷新仍可用。

#### Scenario: 自动刷新总览
- **WHEN** 自动刷新保持开启且经过总览刷新周期
- **THEN** 控制台重新读取总览数据并更新采样时间，未改变页面选择状态

#### Scenario: 实例详情刷新
- **WHEN** 运维人员打开某个实例详情并保持自动刷新
- **THEN** 控制台按实例任务刷新周期重新读取该实例的 active lease，不刷新其他实例的详情数据

### Requirement: 实例清单集中呈现容量和运行状态

控制台 SHALL 将实例容量信息和实例清单合并为单一“实例清单”主视图。清单 SHALL 展示实例 ID、算子类型、设备、模型版本、声明容量、当前在途、有效租约、容量使用率、最近心跳、生命周期和模型就绪状态，并支持算子类型、生命周期、模型就绪、设备/GPU 和是否有活动任务筛选。

#### Scenario: 按算子筛选实例
- **WHEN** 运维人员选择一个算子类型
- **THEN** 清单只显示该算子实例，容量字段和汇总数量同步按筛选结果计算

#### Scenario: 查看容量状态
- **WHEN** 实例返回声明容量、调度使用量和租约数量
- **THEN** 清单展示容量使用率，并同时保留心跳在途和有效租约两个独立观测值

### Requirement: 实例详情显示当前任务归属

控制台 SHALL 在实例详情中展示 `GET /ops/operator-instances/{instance_id}/active-leases` 返回的每条活跃租约及其 `task_id`、`work_type`、`node_id`、`item_id`、`work_id`、`source_service`、上下文状态、获取时间和过期时间。`task_id` 为空的在线请求 SHALL 明确显示为无课程任务。

#### Scenario: 定位实例当前任务
- **WHEN** 运维人员点击某个实例且该实例存在带 `task_id` 的有效租约
- **THEN** 详情列出对应 `task_id` 和 `work_type`，并允许进入该 `task_id` 详情

#### Scenario: 在线请求无课程任务
- **WHEN** 活跃租约的上下文只有在线 `work_type` 且 `task_id` 为空
- **THEN** 详情显示在线请求标识，不创建或猜测课程任务 ID

### Requirement: 任务列表默认展示最新任务并支持分页排序

控制台 SHALL 调用 `GET /ops/course-jobs` 默认参数 `page=1`、`page_size=10`、`sort_by=updated_at`、`order=desc`，展示数据库中最新课程任务。页面 SHALL 支持每页 10、20、50、100 条，按更新时间、创建时间或 `task_id` 排序，切换升降序、上一页/下一页和页码跳转；超过可视区域时列表 SHALL 独立滚动。

#### Scenario: 首次打开任务追踪
- **WHEN** 运维人员进入任务追踪页面
- **THEN** 页面读取第一页最新任务，并按更新时间降序显示，默认每页 10 条

#### Scenario: 改变分页和排序
- **WHEN** 运维人员改变每页数量、排序字段、排序方向或跳转页码
- **THEN** 页面以对应查询参数重新请求服务端，并在响应返回后更新列表和分页总数

### Requirement: task_id 查询进入任务详情

控制台 SHALL 支持通过 `task_id` 查询 `GET /ops/course-jobs/{task_id}`，展示每个 `task_type` 的汇总状态、节点状态、能力、开始/完成时间和错误信息；从实例详情点击任务时 SHALL 复用同一详情视图。

#### Scenario: 通过 task_id 查询
- **WHEN** 运维人员提交有效 `task_id`
- **THEN** 页面展示该课程任务的任务类型分栏及其节点执行情况

#### Scenario: 任务不存在
- **WHEN** Control Service 返回任务不存在或数据库暂不可用
- **THEN** 页面展示明确错误，不显示上一条任务的结果

### Requirement: 网关指标提供会话级实时观测

控制台 SHALL 解析 online-gateway-service `/metrics` 中的请求计数、错误计数、延迟计数/总和、P95 桶估算、容量拒绝和算子分布，并通过相邻采样计算请求速率和错误速率。页面 SHALL 标注这些趋势为当前浏览器会话采样，不得宣称为跨会话历史指标。

#### Scenario: 网关指标采样
- **WHEN** 网关 `/metrics` 连续返回两次累计指标
- **THEN** 控制台根据两次采样的时间差和计数差展示请求速率、错误速率及最新延迟指标

### Requirement: 任务发布和 Kafka 状态可观测

控制台 SHALL 展示 Control Service `/ops/kafka` 返回的 Outbox 待发布数量、Kafka 发布成功累计数、发布失败累计数、消费者积压和发布器状态；该区域 SHALL 明确是只读观测，不提供任务重新发布按钮。

#### Scenario: Kafka 发布链路正常
- **WHEN** `/ops/kafka` 返回发布器可用且消费者积压为零
- **THEN** 页面展示 Outbox、已推送 Kafka、失败数和“消费者正常”状态

#### Scenario: orchestrator 指标不可用
- **WHEN** `/ops/kafka` 返回降级状态
- **THEN** 页面仍展示 Control Service 可获得的 Outbox 待发布数，并将发布成功/积压标记为不可用或降级

### Requirement: GPU 指标和实例部署映射可观测

控制台 SHALL 读取 gpu_metrics_exporter `/gpu`，展示每张 GPU 的编号、型号、利用率、显存、温度、功耗和进程数，并根据实例 `gpu` 标签或实例 ID 显示该 GPU 上部署的算子实例。GPU 指标读取失败不得阻断其他平台数据。

#### Scenario: 读取整机 GPU
- **WHEN** GPU exporter 返回多张 GPU 快照
- **THEN** 页面按 GPU 编号展示当前状态，并列出通过 `gpu` 标签识别出的算子实例

#### Scenario: GPU exporter 不可用
- **WHEN** GPU exporter 不存在、无 NVIDIA 驱动或 NVML 读取失败
- **THEN** 页面显示 GPU 指标不可用，Control Service、任务和网关页面仍可继续使用

### Requirement: GPU 刷新周期可配置

控制台 SHALL 在连接与观测配置中支持 GPU exporter 地址和 GPU 刷新秒数，默认 5 秒，允许范围为 3～30 秒，并将配置保存在当前浏览器。

#### Scenario: 调整 GPU 刷新周期
- **WHEN** 运维人员将 GPU 刷新周期设置为 10 秒并保存
- **THEN** 控制台按 10 秒周期读取 GPU 快照，不改变 Control Service 和网关刷新周期

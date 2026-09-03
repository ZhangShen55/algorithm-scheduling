## 为什么

当前运维控制台已经能够分页读取课程任务并按 `task_id` 查看状态，但任务量增长后缺少任务类型组合、状态粒度、更新时间和模糊 `task_id` 等服务端筛选；详情接口虽然保存了大量算法结果，页面却只展示节点状态，无法支持运维人员快速判断处理规模、耗时、参数和结果质量。与此同时，现有 Kafka 区域只有累计指标，无法回答某个课程任务生成了什么 Outbox 事件、是否收到 Broker 发布确认以及失败原因。

本变更在保持 A 服务和算子既有契约不变的前提下，补齐任务检索、分层详情、标准耗时和 Kafka 发布记录，使控制台从状态看板提升为可用于定位单个课程任务的只读排障工具。

## 变更内容

- 增强现有 `GET /ops/course-jobs`，支持请求过的任务类型组合、课程整体状态、指定任务项状态、更新时间范围和 `task_id` 模糊匹配，并保留服务端分页与排序。
- 任务类型多选采用“同时请求过所有已选类型”的 AND 语义；状态默认筛选课程整体，也允许选择一个具体任务类型后按该任务项状态筛选。
- 前端提供 `10/20/50/100` 每页快捷值和 `1-100` 自定义值，任何筛选变化均回到第一页。
- 将任务详情拆为课程摘要、任务类型详情和大结果按需读取接口，避免全量任务首次进入详情时一次返回 OCR 全文、ASR 全文、行为区间和证据数组。
- 为任务节点透出进入队列、领取、开始和完成时间，并按统一口径返回排队耗时、启动耗时、处理耗时和节点总耗时；历史数据不足时明确返回空值。
- 前端任务详情始终展示状态、进度、关键指标、耗时和失败原因；疑似视频区间、逐页 OCR、完整转写、原始参数、行为区间、逐帧结果及证据默认收起并按需加载，自动刷新不重置展开状态。
- 视频结果中的相对时间统一显示为 `时:分:秒`，运行耗时显示为 `分:秒`，服务端绝对时间继续显示日期和时刻；算法原因代码映射为中文说明。
- 新增 Outbox 事件分页列表、事件详情和按 `task_id` 查询的只读接口，展示事件标识、任务类型、发布状态、创建/可发布/确认时间、尝试次数、最近错误及格式化 payload。
- Kafka 第一版只观测由 Control Service 持久化、orchestrator-service 发布的课程任务 Outbox；不让浏览器直接消费 Kafka，也不宣称覆盖视觉命令和视觉结果等所有 Kafka 消息。
- 连接配置增加基于当前页面主机 IP 的地址模板、恢复默认、三数据源分别测试以及“默认值/浏览器保存值”来源标识。
- 修改完成并通过本地门禁后，在 `192.168.29.11` 构建带 Git revision 的新版 Control Service 和运维控制台镜像、替换容器并执行远端验收；验收通过后按完整 ID 精确删除本次被替代的旧容器和旧镜像。
- 发布完成后补充可复现 Harness 记录，精确暂存本变更，使用中文 Conventional Commit 提交并推送当前 `codex/` 分支。
- 不修改 A 服务 `/api/course-jobs`、在线 HTTP/WebSocket、算子接口、Kafka 消息格式或现有默认端口；不增加发布、重试、取消和容器控制等写操作。

## 能力

### 新增能力

- `algorithm-ops-task-investigation`: 定义课程任务组合筛选、模糊查询、自定义分页、分层详情、节点耗时口径及可折叠算法结果的运维查询能力。
- `algorithm-ops-kafka-event-observability`: 定义基于 PostgreSQL Outbox 的课程任务 Kafka 发布记录、事件详情、按任务追踪和降级表达能力。
- `algorithm-ops-console-connection-presets`: 定义控制台按部署主机生成连接模板、恢复默认、分别测试数据源和标识配置来源的行为。
- `algorithm-ops-release-evidence`: 定义 `192.168.29.11` 镜像与容器升级、旧资产精确清理、Harness 证据及中文规范提交推送要求。

### 修改能力

无。当前主规范尚未包含上述运维控制台细化能力，本变更以新增能力定义；实现时与进行中的 `standardize-ops-console-deployment-and-observability` 保持兼容。

## 影响

- `control_service`：扩展 `/ops/course-jobs` 查询参数；增加任务类型详情、结果和 Outbox 事件只读接口；补齐 Repository 查询模型及接口测试。
- `algorithm-scheduling-platform/packages/platform_common`：扩展课程任务筛选、节点时间和 Outbox 只读查询；不改变 Outbox 写入事务和 Publisher 领取协议。
- `algorithm-scheduling-ops-console`：增加任务筛选栏、自定义分页、分层详情、折叠与按需加载、中文结果映射、Kafka 发布记录和连接模板。
- PostgreSQL：节点时间字段已经存在，预计无需迁移；第一版不持久化 Kafka `topic/partition/offset`，因此也不为发布元数据增加表字段。
- `orchestrator_service`、`vision_orchestrator_service`、`online_gateway_service`、七类算子及 A 服务：业务契约不变；实现期间只进行兼容回归。
- `192.168.29.11` Docker 环境：只替换实际发生代码变化的 Control Service 和运维控制台；新版验收通过后清理其被替代的旧容器和旧镜像，不清理基础设施、算子、GPU exporter、数据卷、模型或 BuildKit 缓存。
- OpenSpec：本变更依赖现有运维控制台基础能力，实施前应避免与 `standardize-ops-console-deployment-and-observability` 的未提交任务文件相互覆盖。

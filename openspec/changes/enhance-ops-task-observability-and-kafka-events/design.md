## 背景

`algorithm-scheduling-ops-console` 当前直接调用 Control Service、online-gateway-service 和 GPU exporter。Control Service 已有 `GET /ops/course-jobs`、`GET /ops/course-jobs/{task_id}` 和 `/ops/kafka` 汇总，但列表只支持分页排序，详情一次返回全部节点结果，Kafka 只返回计数。以 `test_all_0903_15` 为例，现有详情已经包含 18 个 PPT 切片、5 段疑似视频、18 页 OCR、708 个 ASR 分段以及教师/学生行为区间和证据；一次加载并全部展开既难扫描，也会放大响应和渲染成本。

数据库已有 `task_nodes.ready_at/claimed_at/started_at/finished_at` 和完整 `outbox_events`，因此节点耗时与课程任务 Outbox 发布记录不需要引入新的数据源。现有 Repository 和响应模型没有完整透出这些字段，Publisher 也忽略 Kafka Broker 返回的 `topic/partition/offset`。

本变更跨越前端、Control Service 和公共 Repository，但必须保持 A 服务 `/api/course-jobs`、Kafka 消息格式、算子接口及在线路由不变。控制台继续是无需鉴权的内部只读工具。

## 目标与非目标

**目标：**

- 在数据库侧完成任务类型组合、状态、时间范围和模糊 `task_id` 筛选，并返回准确的筛选总数。
- 支持从任务列表进入轻量摘要，再按任务类型和结果区块按需读取详细数据。
- 为节点时间和耗时建立唯一口径，区分绝对运行时间、处理耗时与视频相对时间。
- 以摘要常显、长内容默认收起的方式展示 PPT、OCR、ASR、教师行为和学生行为结果。
- 展示课程任务 Outbox 的待发布、发布中、失败待重试和 Broker 已确认事实，并允许按任务追踪 payload。
- 让连接模板适配控制台实际部署 IP，并独立诊断三个数据源。

**非目标：**

- 不修改 A 服务或算子 HTTP/WebSocket 合同，不增加任务重试、取消、补跑、重新发布或容器控制。
- 不让浏览器连接 PostgreSQL、Kafka 或 orchestrator-service，不创建新的 Kafka Consumer。
- 不在第一版审计视觉命令、视觉进度/完成事件等全部 Kafka 消息。
- 不在第一版持久化或展示 Kafka `partition/offset`；`published_at` 只表达 Producer 收到 Broker 确认。
- 不展示完整媒体、Base64、凭据或未经筛选的日志正文。

## 设计决策

### 1. 增强现有任务列表接口，全部筛选在分页前执行

`GET /ops/course-jobs` 增加以下可选参数：

| 参数 | 语义 |
| --- | --- |
| `task_type` | 可重复，值为四类任务之一；课程必须同时请求过全部已选类型 |
| `overall_status` | 课程整体状态，默认不筛选 |
| `task_status_type` + `task_status` | 指定一种任务类型及其状态，两者必须同时出现 |
| `updated_from` / `updated_to` | ISO 8601 时间，按课程聚合 `updated_at` 闭区间筛选 |
| `task_id_like` | 大小写不敏感的字面子串匹配，转义 `%`、`_` 和转义符 |
| `page` / `page_size` | 页码及每页数量，`page_size` 范围 `1-100` |
| `sort_by` / `order` | 保持现有排序合同 |

Repository 使用聚合 CTE 先计算课程活动时间和整体状态，再使用 `EXISTS` 子查询表达任务类型 AND 条件和任务项状态，最后统计总数、排序和分页。这样筛选不会只作用于当前页，也不会因 join 产生重复课程。

课程整体状态继续复用现有 `_course_job_status` 语义。`task_status_type/task_status` 第一版只接受一组条件；若未来需要表达“ASR 完成且 PPT 失败”，再引入可重复结构化条件，不在本版设计含混的参数编码。

**备选方案：** 前端过滤当前页。该方案会得到错误总数和漏检结果，任务达到万级后不可接受，因此不采用。

### 2. 保留完整详情兼容接口，新增轻量与按需接口

现有 `GET /ops/course-jobs/{task_id}` 保持响应兼容，避免破坏已经部署的控制台或排障脚本。新前端使用：

- `GET /ops/course-jobs/{task_id}/summary`：课程及四类任务摘要、节点摘要、关键计数和耗时，不返回 OCR/ASR 长文本、完整区间、逐帧和证据数组。
- `GET /ops/course-jobs/{task_id}/task-types/{task_type}`：单个任务类型、节点、参数与该类型的结构化摘要。
- `GET /ops/course-jobs/{task_id}/task-types/{task_type}/result`：按需返回该类型完整结果；支持 `node_code` 定位节点，并对 OCR 页、ASR 分段、行为区间和证据等集合使用 `section`、`page`、`page_size` 分块读取，最大每页 100 项。

摘要由 Control Service 进行白名单投影，不由前端下载完整结果后再删减。任务类型和节点不存在分别返回 `404`；不合法的类型、区块或分页返回 `422`；数据库不可用返回 `503`。

**备选方案：** 改变现有详情接口默认响应。虽然代码更少，但属于不必要的兼容风险，因此不采用。

### 3. 节点耗时使用数据库已有时间，不迁移表结构

Repository 的 `NodeRecord` 和节点查询补充 `ready_at` 与 `finished_at`，响应同时返回原始时间和派生毫秒值：

- `queue_wait_ms = claimed_at - ready_at`
- `startup_ms = started_at - claimed_at`
- `processing_duration_ms = finished_at - started_at`
- `total_duration_ms = finished_at - ready_at`

任一计算所需端点为空时，对应耗时返回 `null`；不使用 `updated_at` 替代完成时间。若历史终态行的数据库 `finished_at` 本身为空，本变更不批量猜测或回填。前端将运行耗时格式化为累计 `分:秒`，视频结果 `start_ms/end_ms` 格式化为 `时:分:秒`，绝对时间保留日期和时刻。

### 4. 详情采用两层信息架构，展开状态独立于刷新

课程级摘要和四个任务类型摘要始终可见。每个任务类型最多包含一层“详细结果”折叠；详细结果内部可以按区块分页，但不再创建第三层嵌套折叠。常显内容包括状态、进度、关键指标、节点耗时和失败原因；默认收起内容包括：

- PPT 疑似视频区间和切片明细；
- OCR 逐页摘要及全文；
- ASR 完整转写、分段、速度数组和原始参数 JSON；
- 教师/学生行为区间、逐帧数据和证据明细。

折叠标题携带数量和摘要。展开使用标准 `ChevronDown/ChevronRight`，整个标题行可操作并提供 `aria-expanded`。前端以 `task_id + task_type + section` 保存当前页面内展开状态；自动刷新摘要时不卸载已展开区块、不重置状态，也不重新请求未展开结果。

算法枚举和值代码通过集中映射显示中文；未知代码同时保留原值，防止新后端值被误译。

### 5. Kafka 发布记录以 Outbox 为权威，只读且按需加载 payload

Control Service 增加：

- `GET /ops/kafka/events`：分页返回课程任务 Outbox 摘要，支持 `task_id`、`task_id_like`、`event_type`、`publish_status`、时间范围、分页和排序。
- `GET /ops/kafka/events/{event_id}`：返回单条事件元数据及 payload。
- `GET /ops/course-jobs/{task_id}/events`：返回指定课程的事件时间线。

发布状态按数据库事实派生：`PUBLISHED` 表示 `published_at` 非空；未发布且存在 `last_error` 为 `RETRY_PENDING`；未发布且存在有效领取时间为 `PUBLISHING`；其余为 `PENDING`。接口不暴露 `claim_token`，错误只返回数据库已有的受限摘要。payload 由用户展开后才请求并以格式化 JSON 显示，默认收起。

Outbox 中保存的 envelope 内容就是 Publisher 发送的 `event_id/aggregate_type/aggregate_id/event_type/payload`；`published_at` 表示 `send_and_wait` 成功后落库。因为当前未保存 Producer 返回值，页面明确显示“Broker 已确认”，不显示或猜测 Topic、Partition、Offset，也不把该记录解释为消费者已处理。

**备选方案：** 前端或 Control Service 直接消费 Kafka。该方案需要 Consumer Group、offset 管理、消息保留期和额外权限，且可能影响生产观测，因此不采用。

### 6. 连接模板从页面主机派生，测试结果按数据源隔离

当控制台通过 `http/https` 打开时，以 `window.location.hostname` 生成：

- Control Service：`http://<host>:18100`
- gateway-online：`http://<host>:18103`
- GPU exporter：`http://<host>:9400`

若无法取得可用主机名，则回退构建时默认值。页面分别提供三个模板填入动作和一个“恢复默认”，并标识当前值来自“部署模板”“构建默认”或“浏览器保存”。测试读取独立请求 Control Service、gateway-online 和 GPU exporter，分别显示成功、HTTP/解析错误或连接失败；一个数据源失败不覆盖另外两个结果。

### 7. 目标机采用先验收后精确清理的发布流程

发布目标固定为 `192.168.29.11`。发布前记录当前 Control Service 和运维控制台的容器完整 ID、镜像完整 ID/digest、Compose 身份、Git revision、端口、健康状态和重启次数，并形成保护集。新镜像标签 SHALL 包含版本时间和 Git 短 SHA，镜像内或标签 SHALL 能核对完整 revision。

仅构建和替换本次确有代码变化的服务。新版依次通过 revision、容器健康、Control Service readiness、旧 A 服务合同、任务筛选/详情/Outbox Smoke 和控制台真实数据验收后，才进入清理阶段。清理先输出旧资产 dry-run 清单，再确认每个候选不属于当前容器、不被任何运行中/暂停/停止容器引用，随后按完整容器 ID 删除残留旧容器、重算候选并按完整镜像 ID删除旧镜像。

禁止使用 `docker system prune`、`docker image prune -a`、`docker builder prune`、模糊仓库名匹配或 `docker image rm -f`。PostgreSQL、Redis、Kafka、MongoDB、online-gateway-service、orchestrator-service、vision-orchestrator-service、七类算子、GPU exporter、基础镜像、BuildKit 缓存、volume、模型、`/data/course`、`/data/result` 和历史 Harness 均属于保护集。

若任一构建、替换或验收门禁失败，停止清理并使用保留的旧镜像完整 ID回滚。验收通过并完成旧镜像清理后，本机即时回滚能力被放弃；仍保留旧 Git SHA、Dockerfile、配置摘要和镜像证据，以便按旧 revision 重建。

## 风险与权衡

- [风险] 动态筛选和整体状态聚合可能增加 PostgreSQL 查询成本。→ 为 `task_id/task_type/status/updated_at` 查询路径补充 `EXPLAIN` 验证；只有证据表明确显示需要时才新增索引。
- [风险] 完整 OCR、ASR 和视觉结果仍可能很大。→ 摘要白名单投影、结果区块按需读取且集合分页上限 100，前端不预取折叠内容。
- [风险] 老任务缺少完整时间会显示空耗时。→ 明确显示“暂无精确记录”，不根据 `updated_at` 猜测，不修改历史事实。
- [风险] Outbox payload 可能包含内部路径和算法参数。→ 仅在内部只读控制台按需展示，禁止媒体字节和凭据；保留后续字段级脱敏扩展点。
- [风险] `PUBLISHED` 被误解为业务已消费。→ 页面固定使用“Broker 已确认”，消费者情况仍通过 lag 和任务状态观察。
- [风险] 当前进行中的控制台变更存在未提交文件。→ 实施时精确暂存本变更文件，不覆盖 `standardize-ops-console-deployment-and-observability` 的用户改动。
- [风险] 删除旧镜像会失去本机即时回滚能力。→ 仅在新版全部门禁通过后按完整 ID删除，清理前记录旧 revision、镜像 digest、配置和重建方式；失败时不执行清理。
- [风险] 目标机存在大量平台、算子和基础设施镜像，宽泛清理可能破坏其他业务。→ 使用保护集、dry-run、容器引用复核和精确 ID删除，禁止任何全局 prune。

## 迁移与发布计划

1. 先实现公共 Repository 和 Control Service 查询，运行 Repository、API、数据库不可用与兼容合同测试。
2. 使用真实 `test_all_0903_15` 响应验证四类摘要、结果分页、中文映射和耗时空值语义。
3. 实现前端筛选、详情折叠、连接模板和 Outbox 发布记录，完成构建、空态/错误态及自动刷新不重置测试。
4. 在 `192.168.29.11` 保存旧资产完整清单，构建并先发布 Control Service，执行旧 `/api/course-jobs` 和旧 `/ops/course-jobs/{task_id}` 回归；再发布前端。
5. 远端 Smoke 验证组合筛选、模糊查询、四类详情、Outbox 事件和三个连接测试，并将镜像、容器、响应摘要与回滚点写入 Harness。
6. 全部门禁通过后输出旧资产清理 dry-run，按完整 ID删除本次被替代且无容器引用的旧容器和旧镜像，复核当前容器仍健康并记录释放空间；任一门禁失败则保留旧镜像并回滚。
7. 补齐 Harness 最终证据和未覆盖项，运行 OpenSpec 严格校验及 `git diff --check`，精确暂存后使用中文 Conventional Commit 提交并推送当前 `codex/` 分支。

回滚前端时可保留后端新增只读接口；回滚后端前先回滚前端，避免新前端请求不存在的路径。旧镜像尚未清理时按记录的完整 ID即时回滚；清理后则按保留的旧 Git SHA、Dockerfile 和配置重建。

## 待确认问题

- 当前版本固定任务类型组合为 AND 语义；未来是否需要增加“任一已选类型”的 OR 模式，由实际运维检索反馈决定。
- 是否需要在后续版本保存 Kafka `topic/partition/offset`，应在确有跨系统定位需求时另行提案并迁移数据库。
- OCR 单页全文和行为证据图片是否需要浏览器可访问 URL；当前只有内部文件路径，本变更先展示元数据，不新增文件服务。

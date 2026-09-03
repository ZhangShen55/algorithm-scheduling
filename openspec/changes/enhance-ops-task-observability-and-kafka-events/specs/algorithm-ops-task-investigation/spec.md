## ADDED Requirements

### Requirement: 课程任务列表支持服务端组合筛选
Control Service SHALL 在现有 `GET /ops/course-jobs` 上支持可重复 `task_type`、`overall_status`、成对的 `task_status_type/task_status`、`updated_from/updated_to` 和 `task_id_like` 查询参数，并 SHALL 在分页前应用所有条件、返回筛选后的 `total` 和 `total_pages`。多个 `task_type` SHALL 使用 AND 语义，即课程任务必须请求过全部已选类型。

#### Scenario: 组合筛选四类全量任务
- **WHEN** 运维人员同时选择 `PPT`、`ASR`、`TEACHER_BEHAVIOR` 和 `STUDENT_BEHAVIOR`
- **THEN** 接口只返回同时请求过四类任务的课程，并按筛选后的总数分页

#### Scenario: 默认按课程整体状态筛选
- **WHEN** 运维人员选择“已完成”且状态对象保持默认“课程整体”
- **THEN** 接口按现有课程整体状态聚合口径返回已完成课程，不把单个任务项状态误作整体状态

#### Scenario: 按指定任务项状态筛选
- **WHEN** 运维人员选择状态对象“任务项”、任务类型 `ASR` 和状态“处理中”
- **THEN** 接口返回存在 `ASR` 且该任务项处于处理中状态的课程

#### Scenario: 拒绝不完整任务项状态条件
- **WHEN** 请求只提供 `task_status_type` 或只提供 `task_status`
- **THEN** Control Service 返回 `422` 且不执行含混查询

#### Scenario: 按更新时间闭区间筛选
- **WHEN** 请求提供合法的 `updated_from` 和 `updated_to`
- **THEN** 接口只返回聚合 `updated_at` 位于闭区间内的课程

### Requirement: Task ID 支持安全的模糊查询
Control Service SHALL 将 `task_id_like` 解释为大小写不敏感的字面子串，并 MUST 转义 SQL 通配符和转义字符，不得将用户输入直接拼接到 SQL。前端 SHALL 展示分页匹配列表，用户选择唯一结果后再读取精确详情。

#### Scenario: 模糊查找测试任务
- **WHEN** 运维人员输入 `test_all_0903` 进行模糊查询
- **THEN** 页面展示所有 Task ID 包含该字面子串的分页结果，并允许打开 `test_all_0903_15`

#### Scenario: 输入包含 SQL 通配符
- **WHEN** `task_id_like` 包含 `%`、`_` 或转义字符
- **THEN** Repository 将这些字符按普通字符匹配且查询保持参数化

### Requirement: 任务列表支持受限的自定义分页
任务列表 SHALL 保留 `10/20/50/100` 快捷分页值，并 SHALL 允许输入 `1-100` 的自定义整数。Control Service MUST 拒绝范围外或非整数的 `page_size`；任一筛选条件或每页数量变化时，前端 SHALL 返回第一页。

#### Scenario: 使用自定义每页数量
- **WHEN** 运维人员将每页数量设置为 `30`
- **THEN** 前端请求 `page=1&page_size=30` 且列表最多显示 30 条

#### Scenario: 拒绝过大分页
- **WHEN** 请求设置 `page_size=101`
- **THEN** Control Service 返回 `422`，不会执行无界查询

### Requirement: 任务详情支持摘要和按需结果读取
Control Service SHALL 保持 `GET /ops/course-jobs/{task_id}` 既有响应兼容，并 SHALL 提供课程摘要、单任务类型详情和任务类型结果的只读接口。摘要和任务类型详情 MUST 不包含 OCR/ASR 长文本、完整行为区间、逐帧数据或证据数组；结果接口 SHALL 支持按节点、区块和最多 100 项分页读取集合结果。

#### Scenario: 首次打开全量任务详情
- **WHEN** 运维人员打开 `test_all_0903_15`
- **THEN** 前端首先读取轻量摘要并展示四类任务及节点，不预取完整 OCR、ASR 和视觉结果

#### Scenario: 展开单个结果区块
- **WHEN** 运维人员展开 PPT 疑似视频区间或某一页 OCR 摘要
- **THEN** 前端只请求对应任务类型、节点和结果区块，并显示服务端返回的分页数据

#### Scenario: 旧详情调用保持兼容
- **WHEN** 现有运维脚本继续调用 `GET /ops/course-jobs/{task_id}`
- **THEN** 响应字段和完整结果行为保持兼容，不影响 A 服务独立的 `/api/course-jobs/{task_id}`

### Requirement: 节点时间和耗时具有统一口径
节点摘要 SHALL 返回 `ready_at`、`claimed_at`、`started_at`、`finished_at`，并 SHALL 返回 `queue_wait_ms`、`startup_ms`、`processing_duration_ms` 和 `total_duration_ms`。耗时分别按 `claimed_at-ready_at`、`started_at-claimed_at`、`finished_at-started_at` 和 `finished_at-ready_at` 计算；任一端点缺失时对应值 MUST 为 `null`，不得用 `updated_at` 替代。

#### Scenario: 完整节点耗时
- **WHEN** 一个节点具有完整的进入队列、领取、开始和完成时间
- **THEN** 接口返回四个非负耗时，前端分别标记为排队、启动、算子处理和节点总耗时

#### Scenario: 历史节点缺少完成时间
- **WHEN** 终态历史节点没有 `finished_at`
- **THEN** 处理耗时和总耗时返回 `null`，页面显示“暂无精确记录”

### Requirement: 任务详情采用摘要常显和长内容默认收起
前端 SHALL 始终展示任务与节点状态、进度、关键计数、节点耗时和失败原因；疑似视频区间、逐页 OCR、完整转写、原始参数 JSON、行为区间、逐帧结果和证据明细 SHALL 默认收起。详情不得超过“任务类型”和“详细结果”两层折叠，折叠标题 SHALL 显示数量摘要并使用可访问的展开控件。

#### Scenario: 查看 PPT 摘要
- **WHEN** PPT 切片完成并生成 18 个切片和 5 个疑似视频区间
- **THEN** 页面常显“PPT 识别/切片 18 页”和区间数量，区间明细默认收起

#### Scenario: 自动刷新保持展开状态
- **WHEN** 运维人员展开 ASR 参数或教师行为区间后发生任务摘要自动刷新
- **THEN** 已展开区块保持展开，未展开区块不会被预取或自动打开

### Requirement: 时间与算法原因使用面向运维的中文表达
前端 SHALL 将视频 `start_ms/end_ms` 格式化为 `时:分:秒`，将处理耗时格式化为累计 `分:秒`，并将绝对运行时间显示为日期和时刻。已知算法原因代码 SHALL 映射为中文；未知代码 SHALL 显示可理解的兜底说明并保留原值供排障。

#### Scenario: 显示疑似视频范围
- **WHEN** 疑似播放区间为 `283695ms` 至 `323795ms` 且原因为 `sustained_visual_change`
- **THEN** 页面显示约 `00:04:43 - 00:05:23` 和中文原因“持续画面变化”

#### Scenario: 显示长节点耗时
- **WHEN** 节点处理耗时为 2302 秒
- **THEN** 页面显示累计耗时 `38:22`，不会误显示成一天中的时刻

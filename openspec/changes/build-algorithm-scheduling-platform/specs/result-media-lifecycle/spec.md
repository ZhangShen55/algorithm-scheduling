## ADDED Requirements

### Requirement: 临时路径与持久路径分离
平台 SHALL 将下载的视频、提取的 WAV 文件和普通帧保存到 `/data/course/{task_id}`，并将持久化 PPT 切片和选定的视觉证据保存到 `/data/result/{task_id}`。所有已请求管道进入终态且持久化写入完成后，清理流程 SHALL 只删除临时课程目录。

#### Scenario: 完成后删除临时媒体
- **WHEN** 已请求管道和持久化结果写入全部完成
- **THEN** 删除 `/data/course/{task_id}`，并保留可用的 `/data/result/{task_id}`

### Requirement: 区分文件结果与结构化结果
节点响应 SHALL 只对共享文件系统中确实存在的文件使用 `path` 和 `count`。OCR 文本、关键词、ASR、课程脑图、行为区间和学生统计 SHALL 作为结构化数据库结果保存，并通过节点的 `result` 字段返回。

#### Scenario: PPT 管道完成
- **WHEN** 切片、OCR 和关键词全部完成
- **THEN** `PPT_SLICE` 返回目录路径和数量，`PPT_OCR` 与 `PPT_KEYWORDS` 返回按 `ppt_image_id` 组织的结构化结果，不返回 JSON 文件路径

### Requirement: 保留离线 ASR 响应
`ASR_TRANSCRIPTION.result` SHALL 保留 v1.1.8 成功响应中的 `language`、`segments`、`text`、`speed_info`、`load_audio_time_ms` 和 `gpu_time_ms` 字段，包括由实际 ASR 选项产生的条件性 segment 字段。即使 HTTP 状态为 200，只要 ASR 响应体包含错误 `code` 和 `msg`，适配器 SHALL 将其视为失败。

#### Scenario: 返回完整的 ASR 成功响应
- **WHEN** v1.1.8 返回转写数据
- **THEN** 持久化并返回完整成功响应，不使用平台自定义的转写结构替换它

### Requirement: 保留课程脑图响应
`COURSE_OVERVIEW.result` SHALL 保留现有 `/v1/course_overviews` 成功响应，包括 `model`、`id`、嵌套的 `result.overview`、完成元数据和 token `usage`。嵌套结果的命名 SHALL 保留，不丢弃也不重命名。

#### Scenario: 课程脑图处理成功
- **WHEN** 文本分析返回 `GenericResponse`
- **THEN** 完整响应持久化到节点的平台级 `result` 中

### Requirement: 单张切片的结构化身份
PPT OCR 和关键词结果 SHALL 以 `ppt_image_id` 为键返回结构化项目集合，进度 SHALL 提供 `completed_count` 和 `total_count`。

#### Scenario: 部分切片缺少关键词
- **WHEN** 切片和 OCR 已完成，但关键词处理尚未完成
- **THEN** 切片文件和已完成的 OCR 数据仍可查询，同时关键词节点显示未完成状态和项目进度

### Requirement: 本地路径语义
平台返回的每个 `path` SHALL 表示服务器本地或共享挂载中的绝对文件系统路径，而不是 HTTP URL。除非 A 服务共享或被授予访问该文件系统的权限，否则平台 SHALL 不暗示 A 服务能够直接读取该路径。

#### Scenario: 返回 PPT 切片位置
- **WHEN** A 服务查询已经完成的切片节点
- **THEN** 节点返回类似 `/data/result/course-001/ppt/slices` 的路径，且不将其标记为 URL

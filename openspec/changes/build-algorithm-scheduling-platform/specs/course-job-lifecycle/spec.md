## ADDED Requirements

### Requirement: 稀疏课程任务提交
平台 SHALL 提供 `POST /api/course-jobs`，并要求全局唯一的 `task_id`，以及 `PPT`、`ASR`、`TEACHER_BEHAVIOR`、`STUDENT_BEHAVIOR` 中的一个或多个任务类型。平台只校验所请求任务类型必需的字段，并忽略仅属于未请求任务类型的缺失字段。

#### Scenario: 仅提交 PPT
- **WHEN** A 服务提交 `task_id`、`task_types=["PPT"]` 和 `slides_video_path`，且不携带教师或学生字段
- **THEN** 平台接受请求并且只创建 PPT 管道

#### Scenario: 缺少所选任务的输入
- **WHEN** A 服务提交 `task_types=["STUDENT_BEHAVIOR"]` 但未提供 `student_video_path`
- **THEN** 响应体返回中文校验原因，并且不创建学生行为管道

### Requirement: 任务类型幂等性
平台 SHALL 使用 `(task_id, task_type)` 作为幂等键。已完成的管道直接返回且不重复执行；运行中的管道返回当前状态；此前未请求的任务类型可以追加到同一课程。

#### Scenario: 查询已有的已完成任务类型
- **WHEN** A 服务针对同一 `task_id` 再次提交已经完成的 ASR 任务类型
- **THEN** 平台返回已保存的 ASR 节点状态和结果，不创建新的 ASR 执行

#### Scenario: 追加新的任务类型
- **WHEN** PPT 已完成，A 服务随后针对同一 `task_id` 提交 `TEACHER_BEHAVIOR`
- **THEN** 平台只创建教师行为管道，并保留 PPT 结果

### Requirement: 任务和节点使用整数状态
平台 SHALL 使用整数编码表示任务类型和节点状态：`0` 未请求、`10` 待处理、`20` 等待前置条件、`30` 等待算子、`40` 已入队、`50` 运行中、`60` 已完成、`70` 已失败、`80` 已取消。响应还 SHALL 包含 `status_text` 和中文 `reason`。

#### Scenario: 查询未请求的任务类型
- **WHEN** A 服务查询仅请求了 PPT 的课程
- **THEN** 响应中仍包含 ASR、教师行为和学生行为，并且它们的 `status=0`

### Requirement: 完整课程查询
平台 SHALL 提供 `GET /api/course-jobs/{task_id}`，并在一个响应中返回全部四种任务类型、内部节点、当前状态、可用结果、文件路径、数量、优先级和更新时间。

#### Scenario: 多条管道运行时查询
- **WHEN** ASR 已完成、PPT OCR 正在运行，并且教师行为正在等待 VBas 容量
- **THEN** 查询响应分别展示每条已请求管道的状态和当前节点

### Requirement: 稳定的 ASR 选项
ASR 任务输入 SHALL 接受可选的 `asr_options` 对象。平台 SHALL 将传入值覆盖到默认值 `language=auto`、`showSpk=true`、`showEmotion=true`、`showRoleIdentify=false`、`wordTimestamps=false`、`hotWords=[]` 上，并保存已完成 ASR 节点实际使用的 `effective_params`。

#### Scenario: 覆盖部分 ASR 选项
- **WHEN** A 服务仅提交 `asr_options.showRoleIdentify=true`
- **THEN** ASR 适配器将该值与其余默认值一起发送，节点查询返回合并后的 `effective_params`

#### Scenario: 已有 ASR 结果后参数发生变化
- **WHEN** 使用不同选项再次提交已经完成的 ASR 管道
- **THEN** 平台返回已有结果和原始 `effective_params`，不重新运行 ASR，也不为结果创建新版本

### Requirement: 北向响应信封
面向 A 服务的常规 HTTP 接口 SHALL 返回 HTTP 200，并通过稳定的 `code`、中文 `message` 和 `data` 响应体表达已受理、已存在、校验失败和业务错误等结果。内部健康检查、注册、租约和容量接口 SHALL 保留有意义的 HTTP 状态码。

#### Scenario: 任务类型输入无效
- **WHEN** A 服务遗漏所选任务类型必需的路径
- **THEN** HTTP 响应为 200，响应体包含非成功业务码和中文消息

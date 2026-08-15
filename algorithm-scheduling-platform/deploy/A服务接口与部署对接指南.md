# A 服务接口与部署对接指南

## 1. 对接边界

A 服务只连接两个平台入口：

| 服务 | 默认地址 | 用途 |
|---|---|---|
| `control-service` | `http://127.0.0.1:18100` | 离线课程任务提交和查询 |
| `online-gateway-service` | `http://127.0.0.1:18103` | 在线图片分析和实时 ASR |

A 不直连 PostgreSQL、Kafka、Redis、`orchestrator-service`、`vision-orchestrator-service` 或任何算法实例。平台返回的 `path` 是服务器本地/共享挂载绝对路径，不是 HTTP URL；A 只有在共享该挂载时才能直接读取。

第一阶段接口应部署在可信内网，生产环境由反向代理提供 TLS、鉴权、请求大小限制和访问日志。

当前里程碑 2B 服务器登录合同为 `root@192.168.29.11:22`、密码
`kedacom_123`，不使用 `.env`。用户已批准把部署模板、该登录合同和受控服务默认值保留
在 Git；该例外不包含 SSH 私钥/Deploy Key、模型解密密钥、人脸原图、课程媒体、大型
fixture 或外部可信模型 manifest。

## 2. 通用响应和业务码

正常的 A 面 HTTP API 使用 HTTP 200，并通过响应体表达业务结果：

```json
{
  "code": 0,
  "message": "操作成功",
  "data": {}
}
```

| `code` | 含义 | A 服务处理建议 |
|---:|---|---|
| `0` | 成功/已接收/已存在 | 使用 `data` |
| `40001` | 请求字段或业务参数不合法 | 修正请求，不要原样无限重试 |
| `40401` | `task_id` 不存在 | 按未提交处理或确认业务 ID |
| `50000` | 平台或算子调用失败 | 记录 `message`，按业务策略稍后查询/重试 |
| `50301` | 暂无可用算子容量 | 有界退避后重试；在线请求不会转成离线任务 |

网络错误、连接超时、反向代理错误仍可能使用非 200 HTTP 状态。A 必须同时判断 HTTP 状态和 JSON `code`，不能只判断其中一个。

建议 A 每次请求携带 `X-Trace-ID`；平台会在响应头回传，便于跨服务排查。

## 3. 离线课程任务

### 3.1 提交接口

```text
POST /api/course-jobs
Content-Type: application/json
```

公共字段：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `task_id` | 是 | 课程唯一业务 ID；同一节课后续追加任务继续使用同一值 |
| `task_types` | 是 | 非空数组；可选 `PPT`、`ASR`、`TEACHER_BEHAVIOR`、`STUDENT_BEHAVIOR` |
| `priority` | 否 | `NORMAL` 或 `URGENT`，默认 `NORMAL`；不抢占已运行任务 |

按任务类型校验字段：

| task type | 必填字段 | 可选字段 | 节点 |
|---|---|---|---|
| `PPT` | `slides_video_path` | 无 | `PPT_SLICE -> PPT_OCR -> PPT_KEYWORDS` |
| `ASR` | `teacher_video_path` | `asr_options` | `ASR_TRANSCRIPTION -> COURSE_OVERVIEW` |
| `TEACHER_BEHAVIOR` | `teacher_video_path` | 无 | `TEACHER_BEHAVIOR_ANALYSIS` |
| `STUDENT_BEHAVIOR` | `student_video_path`、`student_count` | `front_points`、`back_point` | `STUDENT_BEHAVIOR_ANALYSIS` |

只校验 `task_types` 选中的任务字段。例如只请求 PPT 时，不需要传教师/学生字段；无关字段缺失或脏值不应影响 PPT。

字段名固定为 `student_count`、`front_points`、`back_point`，不要改名。`student_count` 是应到学生数。`front_points` 与 `back_point` 是 S 画面区域多边形；任一区域未提供时，平台在配置范围内生成一次稳定兜底值，并通过 `front_region_provided`、`back_region_provided` 告知是否由 A 提供区域。

### 3.2 PPT-only 示例

```json
{
  "task_id": "course-001",
  "task_types": ["PPT"],
  "priority": "NORMAL",
  "slides_video_path": "http://media.example/course-001/ppt.mp4"
}
```

### 3.3 ASR 示例与参数

```json
{
  "task_id": "course-001",
  "task_types": ["ASR"],
  "teacher_video_path": "http://media.example/course-001/teacher.mp4",
  "asr_options": {
    "showRoleIdentify": true,
    "hotWords": ["导数", "函数"]
  }
}
```

平台将传入值覆盖到以下默认值，并在 `ASR_TRANSCRIPTION.effective_params` 返回实际使用参数：

```json
{
  "language": "auto",
  "showSpk": true,
  "showEmotion": true,
  "showRoleIdentify": false,
  "wordTimestamps": false,
  "hotWords": []
}
```

平台只调用离线 ASR `/v1.1.8/seacraft_asr`。`language` 去除首尾空白并转为小写后，`auto`、`zh`、`en` 使用 Paraformer；当前唯一小语种白名单 `fr` 使用 Faster-Whisper。

- `fr` 请求 `showSpk=true` 或 `showRoleIdentify=true` 时，每段 `role` 为 `null`；两者均为 `false` 时不返回 `role`。
- `fr` 请求 `showEmotion=true` 时，每段 `emotion` 为 `null`；为 `false` 时不返回 `emotion`。
- `segment_words` 每段始终存在。`wordTimestamps=false` 时为 `[]`；为 `true` 时返回真实词时间，个别无法对齐的段允许为 `[]`。
- 算子未开启小语种或 Whisper 模型未就绪时，`fr` 返回 HTTP 200、`code=4003`；空值或其他未支持语言返回 HTTP 200、`code=4009`。平台将这些响应记为节点业务失败。

`wordTimestamps` 保留但不建议开启。已完成 ASR 再以不同参数提交时，平台复用原结果和原 `effective_params`，不会自动重算或生成新版本。

### 3.4 学生行为示例

```json
{
  "task_id": "course-001",
  "task_types": ["STUDENT_BEHAVIOR"],
  "student_video_path": "http://media.example/course-001/student.mp4",
  "student_count": 38,
  "front_points": [
    {"X": 0, "Y": 0},
    {"X": 1920, "Y": 0},
    {"X": 1920, "Y": 540},
    {"X": 0, "Y": 540}
  ],
  "back_point": [
    {"X": 0, "Y": 540},
    {"X": 1920, "Y": 540},
    {"X": 1920, "Y": 1080},
    {"X": 0, "Y": 1080}
  ]
}
```

前/后排入座率定义为对应区域稳定人数除以画面识别总人数，不依赖座位容量。

### 3.5 组合任务示例

```json
{
  "task_id": "course-001",
  "task_types": ["PPT", "ASR", "TEACHER_BEHAVIOR", "STUDENT_BEHAVIOR"],
  "priority": "URGENT",
  "teacher_video_path": "http://media.example/course-001/teacher.mp4",
  "student_video_path": "http://media.example/course-001/student.mp4",
  "slides_video_path": "http://media.example/course-001/ppt.mp4",
  "student_count": 38
}
```

同一次请求同时选择 ASR 和教师行为时，平台内部共享本次 T 视频下载。以后分开追加会重新从 URL 下载，不为潜在任务长期保留源视频或 WAV。

### 3.6 接收响应与幂等规则

```json
{
  "code": 0,
  "message": "课程任务已接收",
  "data": {
    "task_id": "course-001",
    "tasks": [
      {
        "task_type": "PPT",
        "status": 10,
        "status_text": "待处理",
        "reason": "任务已接收，等待处理",
        "priority": "NORMAL",
        "created": true,
        "updated_at": "2026-08-05T12:00:00Z"
      }
    ]
  }
}
```

幂等键是 `(task_id, task_type)`：

- 首次请求：`created=true`，异步创建所选管道。
- 相同类型处理中：`created=false`，返回当前状态，不重复执行。
- 相同类型已完成：`created=false`，查询接口返回已保存结果，不重复执行。
- 同一 `task_id` 新增类型：只创建此前未请求的类型，保留原结果。

A 遇到提交请求网络超时，可以使用相同 `task_id` 和 `task_types` 安全重试；不要为了重试生成新的课程 ID。

## 4. 查询课程完整状态

```text
GET /api/course-jobs/{task_id}
```

响应固定返回四个 task type；未请求类型为 `status=0`，已请求类型包含节点、优先级、原因、进度和可用结果。

状态码：

| 状态 | 文本 | 含义 |
|---:|---|---|
| `0` | 未请求 | 该课程未选择此类型 |
| `10` | 待处理 | 已接收，等待调度 |
| `20` | 等待前置节点 | 上游节点尚未完成 |
| `30` | 等待算子 | 对应 capability 当前无 ready 容量 |
| `40` | 已排队 | 节点已领取 |
| `50` | 处理中 | 算子或视觉聚合正在执行 |
| `60` | 已完成 | 节点有成功业务结果；无行为也属于完成 |
| `70` | 处理失败 | 节点失败 |
| `80` | 已取消 | 节点取消 |

建议 A 以 2-5 秒间隔轮询，不要高频无间隔请求。每条管道独立：例如 `PPT_SLICE=60`、`PPT_OCR=30` 时，A 已可读取切片路径，但关键词尚不可用。

### 4.1 文件结果与结构化结果

- `PPT_SLICE` 返回 `path/count`，例如 `/data/result/course-001/ppt/slices`。
- `PPT_OCR.result` 按 `ppt_image_id` 返回 OCR 结构化数据和进度。
- `PPT_KEYWORDS.result` 按同一 `ppt_image_id` 返回关键词和进度。
- `ASR_TRANSCRIPTION.result` 保存离线 ASR v1.1.8 完整成功响应；成功顶层保持 `language`、`segments`、`text`、`speed_info`、`load_audio_time_ms`、`gpu_time_ms`，不增加能力状态或成功业务码字段；`effective_params` 单独返回。
- ASR 每段 `speed` 使用 `int(内容数量 × 60 / (ed-bg) × 0.4)`；`speed_info` 保持 1/5/10 分钟窗口统计且不乘 `0.4`。
- `COURSE_OVERVIEW.result` 保存原 `GenericResponse`，其中仍有算法自身的嵌套 `result.overview`。
- `TEACHER_BEHAVIOR.result` 返回板书、坐、站、讲授区间和精选证据。没有某行为时对应数组为 `[]`，状态仍为 60。
- `STUDENT_BEHAVIOR.result` 返回人数、到课率、区域入座率、provided 标识、行为统计和精选证据。

只有真实落地文件返回 `path/count`。OCR、关键词、ASR、脑图和视觉统计不通过本地 JSON path 间接返回。

## 5. 在线图片接口

在线图片由 A 直接提供 Base64。平台不接收 RTSP、不拉流、不截图，也不把请求放入 Kafka。建议一图一请求；兼容多图 VBas 请求时，完整请求只选择一个实例，不跨实例拆分，算子返回的成功项和失败项原样保留。

### 5.1 VBas 师生分析

```text
POST /api/online/vbas/analyze
```

```json
{
  "task_id": "online-001",
  "batch_id": "batch-001",
  "stream_type": "student",
  "ImageList": [
    {
      "ImageId": "student-001",
      "StoragePath": "data:image/jpeg;base64,/9j/4AAQ..."
    }
  ]
}
```

`stream_type` 可用 `student`/`s` 或 `teacher`/`t`。`ImageList` 至少一项，每项必须有非空 `ImageId` 和有效 Base64 `StoragePath`。

### 5.2 人脸对比

```text
POST /api/online/face/recognize
```

```json
{
  "photo": "data:image/jpeg;base64,/9j/4AAQ...",
  "targets": ["T001"],
  "threshold": 0.4
}
```

平台按请求选择一个具有 `recognize` 能力的实例，并把现有算子响应放在平台 `data` 中返回。

### 5.3 图像质量检测

```text
POST /api/online/image-quality/detect
```

```json
{
  "image": "data:image/jpeg;base64,/9j/4AAQ...",
  "include": ["tilt", "screen"],
  "screen_conf": 0.3
}
```

平台选择一个 `detect_all` 实例，按原图像质量算子合同转发。

## 6. 实时 ASR WebSocket

```text
WebSocket /api/online/asr/stream
```

A/播放器建连后发送现有实时 ASR 协议规定的音频帧，平台在连接建立时选择一个 `asr_online` 实例，并在整个会话保持粘性。上游响应实时返回，不默认入课程结果库，也不替代课后离线 ASR。

无容量时平台发送：

```json
{
  "code": 50301,
  "message": "暂无可用实时 ASR 算子容量",
  "data": null
}
```

随后以 WebSocket code `1013` 关闭。上游算子异常中断时使用 `1011`。A 应停止向已关闭会话继续发送音频，按有界退避重新建连。

## 7. 网络与部署连通性

### 7.1 平台进程运行在宿主机

A 与平台同机时使用：

```text
http://127.0.0.1:18100/api/course-jobs
http://127.0.0.1:18103/api/online/...
ws://127.0.0.1:18103/api/online/asr/stream
```

A 在其他机器时，使用平台服务器内网 IP，并只开放 control-service `18100` 和
online-gateway-service `18103`。PostgreSQL `5432`、Kafka `9092`、Redis `6379`、
MongoDB `27017`、orchestrator `18101`、vision `18102` 和全部 24 个算法实例宿主机端口
只绑定 `127.0.0.1`，不对 A 开放。

### 7.2 A 与平台都运行在 Docker

将 A、四个平台服务和算子加入同一个受控 Docker network。A 使用服务名：

```text
http://control-service:18100
http://online-gateway-service:8001
ws://online-gateway-service:8001
```

容器内部不能用 `127.0.0.1` 访问另一个容器。Kafka 固定监听
`EXTERNAL://:9092` 与 `INTERNAL://:29092`，分别广播
`EXTERNAL://127.0.0.1:9092` 与 `INTERNAL://kafka:29092`；宿主机进程使用前者，
平台容器使用 `kafka:29092`。

### 7.3 视频 URL 与本地结果 path

- A 提供的三个视频地址必须能从 orchestrator 所在网络访问，使用 HTTP/HTTPS。
- URL 应在任务实际下载期间保持有效；鉴权令牌、有效期和下载大小限制由双方部署配置确认。
- `/data/result/{task_id}` 若要由 A 直接读取，必须把同一宿主机目录以只读方式挂载给 A，并保持路径一致。
- 若 A 不共享文件系统，当前 `path` 不可直接下载；后续应增加受控文件下载接口或对象存储，不能把本地 path 当成 URL。

## 8. 联调检查清单

1. 用 PPT-only 请求确认稀疏字段校验和异步接收。
2. 重复提交相同 `(task_id, PPT)`，确认 `created=false` 且没有重复切片。
3. 在同一 `task_id` 追加 ASR，确认 PPT 结果保留。
4. 查询运行中组合任务，确认各泳道和节点状态可独立观察。
5. 验证 `PPT_SLICE.path` 在共享挂载上可读，OCR/关键词从 `result` 读取。
6. 验证 ASR 默认参数和覆盖后的 `effective_params`。
7. 验证 `fr` 的条件字段、词时间和语速响应，并验证小语种关闭时 HTTP 200、`code=4003`。
8. 在缺少 front/back 区域时验证 provided 标识为 false，结果多次查询保持稳定。
9. 分别发送 VBas、人脸、图像质量单图 Base64 请求，确认请求级实例分发。
10. 建立实时 ASR 会话，确认同一会话粘性和断开后的容量释放。
11. 使用双方约定的 `X-Trace-ID` 定位一次完整请求日志。

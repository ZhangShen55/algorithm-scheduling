# A 服务对接指南

## 1. 接口边界

A 服务只通过 `control-service` 提交/查询离线课程任务，通过 `online-gateway-service` 调用在线
图片与实时语音能力。A 服务不连接 PostgreSQL、Redis、Kafka、MongoDB 或算法算子实例。

| 场景 | 对接服务 | 是否异步 | 是否进入 Kafka |
| --- | --- | --- | --- |
| PPT、ASR、教师行为、学生行为 | `control-service` | 是 | 是 |
| 在线 VBas、FaceRec、ScreenDet、OCR | `online-gateway-service` | 否 | 否 |
| 实时 ASR | `online-gateway-service` | WebSocket 会话 | 否 |

在线图片由 A 服务直接传 Base64。平台不从 RTSP 或其他流中截图。

当前离线 DAG 为：

- `PPT`：`PPT_SLICE -> PPT_OCR`。
- `ASR`：`ASR_TRANSCRIPTION`。
- 教师/学生行为：各自一个视觉分析节点，由视觉编排服务完成。

新任务不会创建 `PPT_KEYWORDS` 或 `COURSE_OVERVIEW` 占位节点。历史任务中已经存在的退役节点
和结果仍可通过课程查询接口读取。A 服务无需直连数据库，也不应把历史节点是否出现作为新任务
完成条件。

## 2. 在线单图 OCR

```http
POST /api/online/ocr/recognize
Content-Type: application/json
```

请求：

```json
{
  "image_id": "frame-001",
  "image": "data:image/png;base64,...",
  "enable_formula": false
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `image` | 是 | 普通 Base64 或 Data URL；解码后单图不超过 50 MiB |
| `image_id` | 否 | A 服务提供的图片标识；省略时由网关生成 |
| `enable_formula` | 否 | 严格布尔值，默认 `false` |

网关将请求适配为 OCR 算子已有 `/ocr/prediction` 单元素 `key/value` 协议。成功时 OCR 原始响应
对象放在现有 `BusinessResponse.data` 中，保留 `key`、`value`、`formula_results`、`err_no`
和 `err_msg` 语义。

示例响应：

```json
{
  "code": 0,
  "message": "OCR 在线识别完成",
  "data": {
    "key": ["frame-001"],
    "value": ["[]"],
    "formula_results": [],
    "err_no": 0,
    "err_msg": ""
  }
}
```

## 3. 在线业务错误

在线接口保持 HTTP `200`，由业务码表达结果：

| 业务码 | 含义 | A 服务处理建议 |
| ---: | --- | --- |
| `40001` | 请求字段、Base64、72 MiB 正文或 50 MiB 解码边界不合法 | 修正请求，不自动重试同一正文 |
| `50301` | 当前没有可用算子容量 | 可稍后重试；平台没有替 A 服务排队 |
| `50000` | 算子 HTTP、超时或响应格式失败 | 记录请求标识并按 A 服务策略有限重试 |

`50301` 只表示本次在线请求没有取得容量，不表示 Control Service 已创建任务、排队或发布 Kafka。
在线与离线 OCR 使用同一个 `ocr` 实例容量池，不为任一来源预留槽位。

## 4. 图片大小约束

- Online Gateway HTTP 请求正文最大 `75497472` 字节（72 MiB）。
- Base64 解码后的单张图片最大 `52428800` 字节（50 MiB）。
- 超限请求会在申请算子租约前拒绝，不会占用 OCR、VBas、FaceRec 或 ScreenDet 容量。
- 反向代理如存在，请求体上限不得低于 72 MiB。

## 5. 追踪与重试

A 服务应为一次业务调用保留自身请求标识。网关会为每个请求生成或传播 `trace_id`，但不会在
容量租约中保存 Base64、图片内容或识别文本。在线调用不保证实例轮询均衡；只保证按注册、健康
和共享容量选择可用实例。A 服务重试会形成新的在线请求和新租约，不应假设仍命中原实例。
## 6. 离线课程任务详细合同

本节补充 Control Service 的离线北向接口。离线任务只接受视频 URL，平台在后台下载视频并执行
DAG；A 服务不上传视频字节，也不需要访问 PostgreSQL、Kafka、Redis 或算法算子。

### 6.1 入口、请求头和统一响应

宿主机部署时，A 服务使用平台服务器的内网地址：

```text
POST http://<platform-host>:18100/api/course-jobs
GET  http://<platform-host>:18100/api/course-jobs/{task_id}
```

若 A 服务和平台运行在同一个受控 Docker network，可以使用：

```text
http://control-service:18100
```

请求使用 Content-Type: application/json。可以发送 X-Trace-ID，平台会在响应头返回同一个
追踪标识；未发送时平台生成新的标识。追踪标识只用于日志和审计，不会改变任务幂等键。

正常解析到的 A 面 JSON 请求通常返回 HTTP 200，响应结构为：

```json
{
  "code": 0,
  "message": "操作成功",
  "data": {}
}
```

A 必须同时检查 HTTP 状态和 JSON code。网关、反向代理、JSON 解析或网络错误可能返回非 200；
不能把 HTTP 200 当成业务成功，也不能把非 200 当成任务一定没有创建。

离线接口业务码：

| code | 含义 | A 服务处理建议 |
|---:|---|---|
| 0 | 已成功接收、已存在或查询成功 | 读取 data |
| 40001 | 请求字段、任务类型或业务参数不合法 | 修正请求；不要原样无限重试 |
| 40401 | 查询的 task_id 不存在 | 确认业务 ID；不要继续轮询 |
| 50000 | 任务数据库或平台内部错误 | 记录 X-Trace-ID，按业务策略有限重试或告警 |

提交接口不会因为暂时没有算子容量而返回 50301。任务会先接收，随后在查询结果中进入状态 30
（等待算子）。

### 6.2 提交接口

```http
POST /api/course-jobs
Content-Type: application/json
```

公共字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| task_id | string | 是 | 课程业务 ID；建议只使用字母、数字、.、_、-，且首字符为字母或数字 |
| task_types | string[] | 是 | 非空数组，可选 PPT、ASR、TEACHER_BEHAVIOR、STUDENT_BEHAVIOR |
| priority | string | 否 | NORMAL 或 URGENT，默认 NORMAL；URGENT 不抢占已运行节点 |
| teacher_video_path | string | 按任务类型 | 教师视频 HTTP/HTTPS URL |
| student_video_path | string | 按任务类型 | 学生视频 HTTP/HTTPS URL |
| slides_video_path | string | 按任务类型 | PPT 录屏 HTTP/HTTPS URL |
| student_count | integer | 学生行为必填 | 非负整数，不能传布尔值 |
| front_points | object[] | 否 | 学生画面前排区域多边形点，点通常为 {"X": ..., "Y": ...} |
| back_point | object[] | 否 | 学生画面后排区域多边形点 |
| asr_options | object | ASR 可选 | 见下方 ASR 参数；未知字段会被拒绝 |

只有 task_types 中选中的任务类型会校验对应字段。未选择 PPT 时不需要传 slides_video_path；
未选择学生行为时不需要传 student_video_path 和 student_count。

任务类型与字段：

| task_types 值 | 必填字段 | 生成节点 |
|---|---|---|
| PPT | slides_video_path | PPT_SLICE -> PPT_OCR |
| ASR | teacher_video_path | ASR_TRANSCRIPTION |
| TEACHER_BEHAVIOR | teacher_video_path | TEACHER_BEHAVIOR_ANALYSIS |
| STUDENT_BEHAVIOR | student_video_path、student_count | STUDENT_BEHAVIOR_ANALYSIS |

视频 URL 约束：

- 只接受带主机名的 HTTP/HTTPS URL；不接受相对路径、file:// 或 RTSP。
- URL 在任务真正开始下载前必须持续有效。提交成功不代表视频已经下载。
- 下载器默认不跟随 HTTP 重定向；需要鉴权时使用带有效期的签名 URL 或双方约定的可直接访问地址，
  平台不会替 A 服务附加自定义下载请求头。
- 默认单个视频大小上限为 10 GiB；下载连接建立超时默认 10 秒，连续读取超时默认 3600 秒。
- 视频必须能被 ffprobe 解析出正的时长，否则对应任务会异步失败。

安全的 task_id 形式如下，能够避免提交成功后在工作目录创建阶段异步失败：

```text
^[A-Za-z0-9][A-Za-z0-9._-]*$
```

#### PPT 提交示例

```json
{
  "task_id": "course-001",
  "task_types": ["PPT"],
  "priority": "NORMAL",
  "slides_video_path": "https://media.example/course-001/slides.mp4?token=..."
}
```

#### ASR 提交示例

```json
{
  "task_id": "course-001",
  "task_types": ["ASR"],
  "teacher_video_path": "https://media.example/course-001/teacher.mp4?token=...",
  "asr_options": {
    "language": "auto",
    "showSpk": true,
    "showEmotion": true,
    "showRoleIdentify": false,
    "wordTimestamps": false,
    "hotWords": ["导数", "函数"]
  }
}
```

ASR 参数默认值：

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

language 当前支持 auto、zh、en，以及在算子开启小语种模型时的 fr。fr 是否可用由部署模型状态
决定；不支持的语言或未就绪的小语种模型会在异步节点中失败。已完成 ASR 结果不会因后续用不同
asr_options 再次提交而自动重算。

#### 学生行为提交示例

```json
{
  "task_id": "course-001",
  "task_types": ["STUDENT_BEHAVIOR"],
  "student_video_path": "https://media.example/course-001/student.mp4?token=...",
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

student_count 是应到学生数，不是平台从视频中检测出的实际人数。未提供某个区域时，平台会使用
配置中的稳定兜底值，并在结果中返回对应的 front_region_provided 或 back_region_provided。

### 6.3 接收响应、幂等和追加任务

成功接收示例：

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

幂等键为 (task_id, task_type)：

- 首次提交某类型返回 created=true，并创建一次离线管道。
- 相同类型再次提交返回 created=false，不会重复下载、发布或执行。
- 重复提交不会覆盖该类型已经保存的 URL、优先级或 effective_params。
- 同一 task_id 可以后续追加此前未请求的任务类型；已完成类型的结果保留。
- 如果提交请求超时且无法确定服务端是否收到，使用相同 task_id 和 task_types 重试是安全的。
- 当前 A 面没有“强制重算”接口；已失败任务需要由双方约定补跑方式，不要通过无限重复提交期待自动重跑。

提交的 task_types 会去重并保持首次出现顺序。一个请求可以同时提交四种任务：

```json
{
  "task_id": "course-001",
  "task_types": ["PPT", "ASR", "TEACHER_BEHAVIOR", "STUDENT_BEHAVIOR"],
  "priority": "URGENT",
  "slides_video_path": "https://media.example/course-001/slides.mp4",
  "teacher_video_path": "https://media.example/course-001/teacher.mp4",
  "student_video_path": "https://media.example/course-001/student.mp4",
  "student_count": 38
}
```

同一提交中 ASR 和教师行为会复用一次教师视频下载；之后分开的追加提交不保证复用已清理的临时文件。
### 6.4 查询接口和状态机

```http
GET /api/course-jobs/{task_id}
```

成功响应的 data.tasks 固定包含四种任务类型。没有请求的类型也会返回，但状态为 0、nodes 为空：

```json
{
  "code": 0,
  "message": "课程任务查询成功",
  "data": {
    "task_id": "course-001",
    "tasks": [
      {
        "task_type": "PPT",
        "status": 50,
        "status_text": "处理中",
        "reason": "PPT 切片处理中",
        "priority": "NORMAL",
        "effective_params": null,
        "nodes": [
          {
            "node_code": "PPT_SLICE",
            "status": 60,
            "status_text": "已完成",
            "reason": "PPT 切片完成",
            "required_capability": "ppt_slice",
            "progress": {},
            "effective_params": null,
            "claimed_at": "2026-08-05T12:00:02Z",
            "started_at": "2026-08-05T12:00:03Z",
            "updated_at": "2026-08-05T12:02:03Z",
            "path": "/data/result/course-001/ppt/slices",
            "count": 33,
            "result": {}
          },
          {
            "node_code": "PPT_OCR",
            "status": 30,
            "status_text": "等待算子",
            "reason": "等待 OCR 容量",
            "required_capability": "ocr",
            "progress": {},
            "effective_params": null,
            "claimed_at": null,
            "started_at": null,
            "updated_at": "2026-08-05T12:02:04Z"
          }
        ]
      },
      {
        "task_type": "ASR",
        "status": 0,
        "status_text": "未请求",
        "reason": "未请求该任务",
        "priority": null,
        "effective_params": null,
        "nodes": [],
        "updated_at": null
      }
    ]
  }
}
```

完整状态值：

| 状态 | 文本 | 含义 |
|---:|---|---|
| 0 | 未请求 | 该类型没有被选择 |
| 10 | 待处理 | 已写入任务事实，等待发布或领取 |
| 20 | 等待前置节点 | DAG 前置节点尚未完成 |
| 30 | 等待算子 | 当前没有满足条件的算子容量 |
| 40 | 已排队 | 节点已领取，等待实际执行 |
| 50 | 处理中 | 算子或视觉聚合正在执行 |
| 60 | 已完成 | 成功结果已持久化；没有检测到行为也属于完成 |
| 70 | 处理失败 | 节点或任务失败，查看 reason |
| 80 | 已取消 | 节点被取消 |

A 建议每 2-5 秒轮询一次；不要无间隔高频查询。每个 task type 独立推进，例如 PPT_SLICE=60
但 PPT_OCR=30 时，只能读取切片文件，不能认为 OCR 已完成。

nodes 中常用字段：

- node_code：当前实际节点名称；新任务不会创建已退役的 PPT_KEYWORDS 或 COURSE_OVERVIEW。
- progress：执行中的进度或租约信息，字段随节点类型不同。
- effective_params：该节点真正采用的参数，ASR 重点查看此字段。
- claimed_at、started_at：数据库时间；尚未领取或启动时为 null。
- path、count：仅文件型结果可能出现，不能把 path 当成 HTTP URL。
- result：结构化结果；不存在时不要按空对象强行推断成功。

查询不存在的课程返回 HTTP 200、code=40401。任务进入 70 时，A 应保存 reason、节点码和
X-Trace-ID，不能只记录“任务失败”。当前 A 面没有取消课程任务的公共接口；状态 80 只表示平台
内部已产生取消事实。

### 6.5 离线结果格式与文件生命周期

| 节点 | 结果重点 |
|---|---|
| PPT_SLICE | path、count、result 中的 manifest 元数据；切片位于 {result_root}/{task_id}/ppt/slices |
| PPT_OCR | 按 ppt_image_id 索引的结构化 OCR 结果，包含文本和算子原始响应 |
| ASR_TRANSCRIPTION | language、segments、text、speed_info、load_audio_time_ms、gpu_time_ms |
| TEACHER_BEHAVIOR_ANALYSIS | 板书、坐、站、讲授区间以及精选证据 |
| STUDENT_BEHAVIOR_ANALYSIS | 人数、到课率、区域入座率、区域 provided 标识和行为统计 |

PPT OCR 中每张图片的 value 是 JSON 字符串，解析后才是结果数组；不要把它当成已经展开的数组。
ASR 的 segments 结构保持离线 ASR v1.1.8 原始合同，平台不把完整 ASR 文本放进日志。

平台默认使用：

```text
/data/course/{task_id}   临时下载视频、WAV、普通抽帧；全部任务终态后可清理
/data/result/{task_id}   PPT 切片和精选视觉证据；正常流程长期保留
```

path 是平台服务器或共享文件系统中的绝对路径。A 如果需要直接读取 PPT 图片，必须和平台共享
同一挂载，并保持路径一致；没有共享挂载时不能把该值拼成 URL。平台不会把 path 自动转换为下载
链接，也不会把视频或图片 Base64 放进课程查询响应。

### 6.6 离线提交与查询重试矩阵

| 情况 | 建议 |
|---|---|
| HTTP 超时，无法确认是否接收 | 使用相同 task_id 和任务类型重试；随后查询确认 |
| HTTP 200、code=40001 | 修正字段，不重试原正文 |
| HTTP 200、code=40401 | 检查课程 ID，不继续轮询 |
| HTTP 200、code=50000 | 有界退避；不要并发制造大量相同提交 |
| 已接收后状态 30 | 继续轮询；这是排队状态，不是提交失败 |
| 已接收后状态 70 | 记录节点 reason；当前无公共强制重算接口 |
| 查询网络错误 | 按 2-5 秒退避重试，避免把未知状态当成未提交 |

## 7. 在线 JSON 接口详细合同

在线图片接口不创建课程任务、不写课程 DAG，也不进入 Kafka。每个推理请求通常申请一个算子容量
租约，人物库管理是例外：它固定调用配置中的一个 FaceRec 管理实例，不申请推理租约。

### 7.1 在线图片公共规则

以下四条推理路由受网关图片限制：VBas、FaceRec 识别、ScreenDet、OCR。

- HTTP 请求体上限默认 75,497,472 字节（约 72 MiB）。
- 单张 Base64 解码后上限默认 52,428,800 字节（50 MiB）。VBas 多图请求是“总正文 72 MiB + 每张 50 MiB”。
- 支持严格标准 Base64，或 data:image/...;base64,... Data URL；必须能被 Pillow 作为完整图片加载。
- 图片在申请算子租约前校验，校验失败不占用推理容量。
- 人物库管理路由当前不经过上述图片大小中间件，A 不应把推理路由的 72/50 MiB 限制误套到人物管理。

所有在线 JSON 响应外层形如：

```json
{
  "code": 0,
  "message": "操作完成",
  "data": {}
}
```

外层 code=0 只表示网关完成 HTTP 调用并得到 JSON 对象，不自动表示算子业务成功。A 必须按照每个
接口的内层字段判断；尤其要处理部分成功和未匹配等正常业务状态。

### 7.2 VBas 师生行为分析

```http
POST /api/online/vbas/analyze
Content-Type: application/json
```

请求：

```json
{
  "task_id": "online-001",
  "batch_id": "batch-001",
  "stream_type": "student",
  "ImageList": [
    {
      "ImageId": "student-001",
      "StoragePath": "data:image/jpeg;base64,/9j/4AAQ...",
      "frame_id": "frame-001",
      "frame_index": 0,
      "timestamp_seconds": 0.0
    }
  ]
}
```

规则：

- stream_type 支持 student、s、teacher、t，大小写和首尾空白会规范化。
- ImageList 至少一项；每项必须有非空 ImageId 和 StoragePath。
- A 服务的 StoragePath 必须是 Base64 或 Data URL，不要传平台本机文件路径。
- Points、frame_id、frame_index、timestamp_seconds 可随请求透传；教师请求可透传 ReturnHeadPose，
  阈值覆盖字段按对应 VBas 算子版本执行。
- 多图片请求整体选择一个 VBas 实例，不会拆到多个实例；返回顺序保留。

典型响应：

```json
{
  "code": 0,
  "message": "VBas 在线分析完成",
  "data": {
    "StatusObject": {"StatusString": "partial", "StatusCode": 0},
    "DataList": [
      {
        "StatusObject": {
          "ImageId": "student-001",
          "StatusString": "success",
          "StatusCode": 0
        },
        "ResultList": []
      }
    ]
  }
}
```

外层 data.StatusObject.StatusCode 和每个 DataList[i].StatusObject.StatusCode 都要检查。外层
成功但某张图片 StatusCode != 0 时属于部分失败，A 应按图片 ID 重试或记录，不要重发整个成功项集合。

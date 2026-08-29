# A 服务对接指南

## 1. 接口边界

A 服务（上游调用方）只通过 `control-service` 提交/查询离线课程任务，通过
`online-gateway-service` 调用在线图片与实时语音能力。A 服务不连接 PostgreSQL、Redis、Kafka、
MongoDB 或算法算子实例。

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

### 1.1 ARCH-001：历史总体组件图（七算子裁剪视图）

下图沿用总体设计 ARCH-001 的一体化布局，用于展示调用方、四个平台服务、基础设施、共享目录和
算法实例池之间的总体关系。为符合当前平台边界，本图只保留七类现役算子；具体接口、数据流和部署
约束以本文后续章节为准。

```mermaid
flowchart TB
    subgraph U["调用方"]
        A["A 服务（上游调用方）\n离线提交与查询"]
        O["在线 Base64 图片"]
        L["直播音频流"]
        OP["运维人员"]
    end
    subgraph P["算法调度平台"]
        C["control-service\n任务、状态、Outbox、注册与租约"]
        R["orchestrator-service\nPublisher、Consumer、DAG、通用执行"]
        V["vision-orchestrator-service\n动态抽帧、多轮检测、聚合"]
        G["online-gateway-service\n在线图片与实时 ASR 代理"]
    end
    PG[("PostgreSQL\n任务、节点、结果、Outbox")]
    K[("Kafka\n离线命令、进度、完成事件")]
    RD[("Redis\n心跳、租约、会话绑定")]
    TEMP[("/data/course/{task_id}\n临时媒体")]
    RESULT[("/data/result/{task_id}\n长期图片")]
    subgraph ALG["算法实例池"]
        AO["asr_offline"]
        AI["asr_online"]
        PS["ppt_slice"]
        OC["ocr"]
        VB["vbas"]
        FA["facerec"]
        SQ["screen_det"]
    end
    A --> C
    C --> PG
    R --> PG
    R <--> K
    R --> TEMP
    R --> AO
    R --> PS
    R --> OC
    K --> V
    V -->|"同步帧级 HTTP"| VB
    V --> PG
    V --> RESULT
    O --> G
    L --> G
    G --> VB
    G --> FA
    G --> SQ
    G --> OC
    G --> AI
    OP --> C
    C <--> RD
    ALG -. "主动注册与心跳" .-> C
    C -. "实例与容量" .-> R
    C -. "实例与容量" .-> V
    C -. "实例与容量" .-> G
```

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

已进入网关路由并被映射的在线业务错误通常保持 HTTP `200`，由业务码表达结果：

| 业务码 | 含义 | A 服务处理建议 |
| ---: | --- | --- |
| `40001` | 请求字段、Base64、72 MiB 正文或 50 MiB 解码边界不合法 | 修正请求，不自动重试同一正文 |
| `50301` | 在线算子租约申请或续租失败；最常见是当前无可用容量 | 有界退避后重试；持续出现时保留追踪标识并告警 |
| `50000` | 算子 HTTP、超时或响应格式失败 | 记录请求标识并按 A 服务策略有限重试 |

`50301` 是当前租约异常的统一映射，A 不能只凭该码区分真实容量不足、Control 调用/响应解析失败或
续租失败。它不表示 Control Service 已创建离线任务、排队或发布 Kafka；在线网关不会替 A 保留
一个等待稍后执行的在线请求。
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

A 必须同时检查 HTTP 状态和 JSON code。Control Service 已识别到的 JSON/Pydantic 请求校验错误
会映射为 HTTP 200、code=40001；反向代理、路由错误、网络故障或未捕获的应用异常仍可能返回
非 200。不能把 HTTP 200 当成业务成功，也不能把非 200 当成任务一定没有创建。

离线接口业务码：

| code | 含义 | A 服务处理建议 |
|---:|---|---|
| 0 | 已成功接收、已存在或查询成功 | 读取 data |
| 40001 | 请求字段、任务类型或业务参数不合法 | 修正请求；不要原样无限重试 |
| 40401 | 查询的 task_id 不存在 | 确认业务 ID；不要继续轮询 |
| 50000 | 已映射的任务仓储或平台内部错误 | 记录 X-Trace-ID，按业务策略有限重试或告警 |

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
| task_id | string | 是 | 课程业务 ID，长度 1-200；必须满足下方安全正则 |
| task_types | string[] | 是 | 非空数组，可选 PPT、ASR、TEACHER_BEHAVIOR、STUDENT_BEHAVIOR |
| priority | string | 否 | NORMAL 或 URGENT，默认 NORMAL；URGENT 不抢占已运行节点 |
| teacher_video_path | string | 按任务类型 | 教师视频 HTTP/HTTPS URL |
| student_video_path | string | 按任务类型 | 学生视频 HTTP/HTTPS URL |
| slides_video_path | string | 按任务类型 | PPT 录屏 HTTP/HTTPS URL |
| student_count | integer | 学生行为必填 | 非负整数，不能传布尔值 |
| front_points | object[] | 否 | 学生画面前排区域多边形点；每个点使用整型 X、Y，坐标系与原视频画面一致 |
| back_point | object[] | 否 | 学生画面后排区域多边形点；字段名固定为单数 back_point |
| asr_options | object | ASR 可选 | 见下方 ASR 参数；未知字段会被拒绝 |

只有 task_types 中选中的任务类型会校验对应字段。未选择 PPT 时不需要传 slides_video_path；
未选择学生行为时不需要传 student_video_path 和 student_count。Control Service 当前只同步检查
front_points/back_point 是否存在，不检查其内部结构；对象列表结构和每个点的整型 X/Y 会在异步
视觉链路中继续校验。区域格式错误可能先返回 code=0，随后任务状态变为 70，因此 A 必须按本表
自行前置校验。

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
- 默认单个视频大小上限为 10 GiB。媒体下载的实际 HTTP 超时当前跟随 Orchestrator 的统一算子
  HTTP 客户端，由部署参数决定；A 不应依赖配置文件中尚未接入下载链路的 10/3600 秒字段。
- 视频必须能被 ffprobe 解析出正的时长，否则对应任务会异步失败。

task_id 必须满足以下形式：

```text
^[A-Za-z0-9][A-Za-z0-9._-]*$
```

当前同步入口只校验长度，工作目录创建时才执行上述硬校验。中文、空格、斜杠或首字符不是字母/
数字的 ID 可能先被接收，再异步失败；A 不得使用这类 ID。

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

student_count 是应到学生数（来自上游课表数据），不是平台从视频中检测出的实际人数。未提供某个区域时，平台会使用
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

同一提交中 ASR 和教师行为会复用一次教师视频下载；之后分开的追加提交会生成新的 submission_id
和下载目录，并重新从 URL 下载媒体。追加任务时必须重新确认签名 URL 有效期和可达性。

### 6.4 查询接口和状态机

```http
GET /api/course-jobs/{task_id}
```

成功响应的 data.tasks 固定包含四种任务类型。没有请求的类型也会返回，但状态为 0、nodes 为空。
以下示例节选 PPT 和 ASR 两项，真实响应还会包含 TEACHER_BEHAVIOR 和 STUDENT_BEHAVIOR：

```json
{
  "code": 0,
  "message": "课程任务查询成功",
  "data": {
    "task_id": "course-001",
    "tasks": [
      {
        "task_type": "PPT",
        "status": 30,
        "status_text": "等待算子",
        "reason": "等待算子能力可用: ocr",
        "priority": "NORMAL",
        "created": false,
        "effective_params": null,
        "nodes": [
          {
            "node_code": "PPT_SLICE",
            "status": 60,
            "status_text": "已完成",
            "reason": "PPT 切片处理完成",
            "priority": "NORMAL",
            "required_capability": "ppt_slice",
            "progress": {
              "task_id": "course-001",
              "operator_task_id": "ppt-node-101",
              "lease_status": "TERMINAL_PERSISTED",
              "completed_count": 1,
              "total_count": 1
            },
            "effective_params": null,
            "claimed_at": "2026-08-05T12:00:02Z",
            "started_at": "2026-08-05T12:00:03Z",
            "updated_at": "2026-08-05T12:02:03Z",
            "path": "/data/result/course-001/ppt/slices",
            "count": 1,
            "result": {
              "manifest_path": "/data/result/course-001/ppt/manifest.json",
              "images": [
                {
                  "ppt_image_id": "ppt-0123456789abcdef0123456789abcdef",
                  "frame_seq": 1,
                  "snap_time": 12,
                  "path": "/data/result/course-001/ppt/slices/ppt-0001-f1-t12s.jpg"
                }
              ],
              "dynamic_segments": []
            }
          },
          {
            "node_code": "PPT_OCR",
            "status": 30,
            "status_text": "等待算子",
            "reason": "等待算子能力可用: ocr",
            "priority": "NORMAL",
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

A 建议每 2-5 秒轮询一次；不要无间隔高频查询。查询已请求的任务类型时，created 固定为 false，
表示本次查询没有创建新任务，不能用它判断该任务最初是否成功创建。每个 task type 独立推进，例如 PPT_SLICE=60
但 PPT_OCR=30 时，只能读取切片文件，不能认为 OCR 已完成。

任务类型状态是其节点状态的聚合，并不保证逐值复制某个子节点。例如节点 40（已排队）通常聚合为
任务类型 50（处理中）；节点 20、30、40 的精确阶段应从 nodes 读取。

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
| PPT_SLICE | path、count、result 中的 manifest 元数据；切片位于 `{result_root}/{task_id}/ppt/slices` |
| PPT_OCR | 按 ppt_image_id 索引的结构化 OCR 结果，包含文本和算子原始响应 |
| ASR_TRANSCRIPTION | language、segments、text、speed_info、load_audio_time_ms、gpu_time_ms |
| TEACHER_BEHAVIOR_ANALYSIS | 板书、坐、站、讲授区间以及精选证据 |
| STUDENT_BEHAVIOR_ANALYSIS | 人数、到课率、区域入座率、区域 provided 标识和行为统计 |

PPT OCR 每项 result 中的 ocr_response.value[0] 是 JSON 字符串，解析后才是普通 OCR 结果数组；
同项的 text 是平台提取并用换行拼接后的便捷文本。不要把 value[0] 当成已经展开的数组。
ASR 的 segments 结构保持离线 ASR v1.1.8 原始合同，平台不把完整 ASR 文本放进日志。但课程
查询会一次性返回完整 segments 和 text，当前没有分页；长课程响应可能很大，A 需要配置合理的
客户端响应大小、读取超时和存储方式。

#### 6.5.1 PPT Slice 与 PPT OCR

以下 JSON 只展示节点的 result 字段。节点未完成时 result 可能缺失；A 应先判断节点状态，再解析
结果，并忽略未来新增的未知字段。

PPT_SLICE.result 的完整字段结构为：

```json
{
  "manifest_path": "/data/result/course-001/ppt/manifest.json",
  "images": [
    {
      "ppt_image_id": "ppt-0123456789abcdef0123456789abcdef",
      "frame_seq": 1,
      "snap_time": 12,
      "path": "/data/result/course-001/ppt/slices/ppt-0001-f1-t12s.jpg"
    }
  ],
  "dynamic_segments": [
    {
      "type": "SUSPECTED_VIDEO_PLAYBACK",
      "start_ms": 32000,
      "end_ms": 45000,
      "confidence": 0.91,
      "reason": "sustained_visual_change"
    }
  ]
}
```

- 节点外层 path 指向切片目录，count 等于 images 长度。
- manifest_path、images[i].path 都是共享文件系统绝对路径。
- snap_time 是整数秒；dynamic_segments 使用毫秒半开区间，必须满足 start_ms < end_ms。
- images 和 dynamic_segments 都可能为空数组；是否成功仍以节点 status 判断。

PPT_OCR.result 是以 ppt_image_id 为键的对象：

```json
{
  "ppt-0123456789abcdef0123456789abcdef": {
    "ppt_image_id": "ppt-0123456789abcdef0123456789abcdef",
    "text": "第一章 函数",
    "ocr_response": {
      "err_no": 0,
      "err_msg": "",
      "key": ["ppt-0123456789abcdef0123456789abcdef"],
      "value": [
        "[{\"text\":\"第一章 函数\",\"confidence\":0.99,\"text_region\":[[10,20],[300,20],[300,60],[10,60]]}]"
      ],
      "formula_results": []
    }
  }
}
```

只有 ocr_response.value[0] 需要二次 JSON 解析；外层 text 已是平台提取的便捷文本。每完成一张图片，
平台会在同一事务中把该 ppt_image_id 的结果合并到节点 result，并更新
progress.completed_count/total_count。因此 PPT_OCR 运行中或后续失败后，公共查询可能已返回
部分图片映射；A 可按业务需要保留这些部分结果，但只有 PPT_OCR.status=60 才表示整批成功。

#### 6.5.2 ASR

ASR_TRANSCRIPTION.result 是算子成功对象本身，不再额外包一层业务 code：

```json
{
  "language": "auto",
  "segments": [
    {
      "segment_text": "一二三四五六七八九十",
      "bg": "0.00",
      "ed": "60.00",
      "speed": 4,
      "segment_words": [],
      "role": "teacher",
      "emotion": "平淡"
    }
  ],
  "text": "一二三四五六七八九十",
  "speed_info": [
    {
      "unit": 1,
      "segment_info": {"segment_count": 1, "speed": [10]}
    },
    {
      "unit": 5,
      "segment_info": {"segment_count": 1, "speed": [10]}
    },
    {
      "unit": 10,
      "segment_info": {"segment_count": 1, "speed": [10]}
    }
  ],
  "load_audio_time_ms": "163.24",
  "gpu_time_ms": "1349.49"
}
```

segments 成功时为非空数组，segment_words 总是存在；wordTimestamps=false 时它为 []。role 和
emotion 是否出现取决于 ASR 开关和语言模型，允许为 null。bg/ed 和两个耗时字段当前是字符串，
A 应按需安全转换，不要假设它们是 JSON number。段级 speed 使用该段时长并乘部署的
speech_rate_factor；speed_info 则按 1/5/10 分钟时间窗统计，不乘该修正因子，两者数值不应被
期待相等。上例按 60 秒内 10 个字、speech_rate_factor=0.4 展示这一差异。实际请求参数读取
节点 effective_params，语速修正因子由 ASR 部署配置决定。

#### 6.5.3 教师行为

TEACHER_BEHAVIOR_ANALYSIS.result：

```json
{
  "analysis_quality": "SUFFICIENT",
  "valid_frame_count": 17,
  "total_frame_count": 17,
  "valid_frame_ratio": 1.0,
  "writing_intervals": [
    {"start_seconds": 10.0, "end_seconds": 18.0}
  ],
  "sitting_intervals": [],
  "standing_intervals": [
    {"start_seconds": 0.0, "end_seconds": 10.0}
  ],
  "teaching_intervals": [],
  "duration_seconds": 60.0,
  "scan": {
    "stages": ["coarse_10s", "topology_5s", "topology_2s"],
    "candidate_windows": [[0.0, 20.0]],
    "evaluated_point_count": 17
  },
  "evidence": [
    {
      "category": "teacher_writing",
      "capture_second": 12.0,
      "confidence": 0.92,
      "path": "/data/result/course-001/vision/teacher_writing/teacher_writing-000000012000.jpg"
    }
  ]
}
```

顶层字段：

| 字段 | 类型/取值 | 含义 |
|---|---|---|
| `analysis_quality` | string | 分析覆盖质量；当前只有 `SUFFICIENT` 和 `INSUFFICIENT_VALID_FRAMES` |
| `valid_frame_count` | integer，`>= 0` | 成功解析并进入聚合的采样时间点数，不是检测到教师行为的帧数，也不是原视频总帧数 |
| `total_frame_count` | integer，`> 0` | 聚合器用于质量判定的采样点总数，不是按视频 FPS 计算的物理帧总量 |
| `valid_frame_ratio` | number，`0-1` | `valid_frame_count / total_frame_count`，当前不额外四舍五入 |
| `writing_intervals` | object[] | 教师板书区间，来源于 VBas 教师 `ObjectType=203` |
| `sitting_intervals` | object[] | 教师坐姿区间，来源于 VBas 教师 `ObjectType=201` |
| `standing_intervals` | object[] | 教师站姿区间，来源于 VBas 教师 `ObjectType=202` |
| `teaching_intervals` | object[] | 教师讲授区间，来源于 VBas 教师 `ObjectType=204` |
| `duration_seconds` | number，`> 0` | ffprobe 读取的原视频真实时长，单位秒；不是四类行为时长之和 |
| `scan` | object | 本次教师自适应抽帧和检测计划的执行摘要，不是行为统计结果 |
| `evidence` | object[] | 已筛选并持久化的教师代表帧；没有入选图片时为 `[]` |

四类 `*_intervals` 的元素结构一致：

| 子字段 | 类型 | 含义 |
|---|---|---|
| `start_seconds` | number | 行为区间起点，相对视频起点的秒数，包含该时刻 |
| `end_seconds` | number | 行为区间终点，相对视频起点的秒数，不包含该时刻 |

区间采用 `[start_seconds, end_seconds)` 半开语义，满足
`0 <= start_seconds < end_seconds <= duration_seconds`。这些边界由离散采样点推算，不是逐视频帧
检测得到的精确边界。当前板书区间间隔不超过 3 秒时合并，坐姿区间间隔不超过 5 秒时合并；站姿和
讲授只合并重叠或首尾相接的区间。四类行为独立聚合，区间可能重叠，A 不应把四类时长直接相加作为
教师有效出镜时长。

`scan` 子字段：

| 子字段 | 类型 | 含义 |
|---|---|---|
| `stages` | string[] | 实际执行的扫描阶段；至少有 `coarse_{间隔}s`，粗扫命中板书或坐姿后才出现 `topology_{间隔}s` 加密阶段 |
| `candidate_windows` | number[2][] | 粗扫命中板书/坐姿后形成的加密检测窗口，两个值分别是窗口起止秒数；无粗扫命中时为 `[]`，它不是最终行为区间 |
| `evaluated_point_count` | integer，`> 0` | 去重后实际交给 VBas 评估的时间点数，不是行为命中数，也不是 HTTP 请求次数 |

当前默认扫描阶段是 `coarse_10s`，存在候选窗口时继续执行 `topology_5s`、`topology_2s`；部署或内部
策略可以调整间隔。只有板书或坐姿粗扫命中会触发加密扫描，站姿或讲授单独命中不会触发，因此四类
区间都应按采样估计值读取。`evaluated_point_count` 还受检测点上限保护，当前部署默认上限为 10000；
超过上限时节点失败，不会截断后冒充完整结果。

`evidence` 元素字段：

| 子字段 | 类型/取值 | 含义 |
|---|---|---|
| `category` | string | 当前教师结果只可能是 `teacher_writing`、`teacher_sitting`、`teacher_teaching`；当前不生成 `teacher_standing` 证据 |
| `capture_second` | number，`>= 0` | 代表帧在视频中的采样时刻，单位秒 |
| `confidence` | number，`0-1` | 对应行为对象列表中的最大模型置信度；行为计数为正但没有可解析置信度时，当前回退为 `1.0` |
| `path` | string | 已复制到配置结果根目录下的持久图片绝对路径；标准部署为 `/data/result/{task_id}/vision/...`，不是 HTTP URL |

`analysis_quality=SUFFICIENT` 只表示当前满足至少 5 个有效采样点且有效比例不低于 0.5，不保证一定
检测到教师或某种行为。当前成功执行路径中 `valid_frame_count` 和 `total_frame_count` 都取实际已
评估点数，因此两者恒相等且 `valid_frame_ratio=1.0`；当前质量不足实际由采样点少于 5 触发。任一
VBas 单帧响应失败会使节点失败，而不是记为无效帧后继续返回部分教师结果。

质量足够但没有检测到目标行为时，四类 intervals 为 `[]`；质量不足时四类 intervals 也会统一清空，
两者都可能得到节点 `status=60`，必须结合 `analysis_quality` 区分。证据图片独立筛选，所以质量不足、
区间被清空时仍可能保留少量命中的 `evidence`。默认同类证据 30 秒内只保留更高置信度者，每类最多
3 张、单任务最多 20 张；这些是部署参数，`evidence` 不是所有命中帧的完整清单。

上述字段都位于节点 `result` 内。节点成功完成时这些字段均存在且不为 null，集合没有数据时使用
空数组。节点外层 `count` 是持久证据图片数量，包括 `0`；只有证据非空时才返回外层 `path`，其值
是该任务的视觉结果目录。

#### 6.5.4 学生行为

STUDENT_BEHAVIOR_ANALYSIS.result：

```json
{
  "student_count": 38,
  "recognized_total_person_count": 30.0,
  "stable_person_count": 24.0,
  "attendance_rate": 0.631579,
  "front_occupancy_ratio": 0.266667,
  "back_occupancy_ratio": 0.31,
  "front_region_provided": true,
  "back_region_provided": false,
  "duration_seconds": 60.0,
  "sample_interval_seconds": 10.0,
  "frames": [
    {
      "timestamp_seconds": 0.0,
      "detected_total": 30,
      "stable_person_count": 24,
      "phone_count": 1,
      "sleep_count": 2,
      "read_count": 4
    }
  ],
  "evidence": [
    {
      "category": "student_phone_use",
      "capture_second": 0.0,
      "confidence": 0.033333,
      "path": "/data/result/course-001/vision/student_phone_use/student_phone_use-000000000000.jpg"
    }
  ]
}
```

顶层字段：

| 字段 | 类型/取值 | 含义 |
|---|---|---|
| `student_count` | integer，`>= 0` | A 提交的应到学生人数原样回显，不是模型检测人数 |
| `recognized_total_person_count` | number，`>= 0` | 在全图检测人数大于 0 的采样帧中，对 `detected_total` 取中位数；无有效人数帧时为 `0.0` |
| `stable_person_count` | number，`>= 0` | 在同一批有效采样帧中，对 VBas `ObjectType=101` 的人脸/抬头人数取中位数；不是跨帧身份去重人数 |
| `attendance_rate` | number，`0-1` | `stable_person_count / student_count`，限制到 `0-1` 并保留最多 6 位小数；`student_count=0` 时为 `0.0` |
| `front_occupancy_ratio` | number，`0-1` | 前排区域入座比例；实测或兜底的具体口径见下文 |
| `back_occupancy_ratio` | number，`0-1` | 后排区域入座比例；实测或兜底的具体口径见下文 |
| `front_region_provided` | boolean | A 是否提交了非空 `front_points`；`false` 表示前排比例不是区域实测值 |
| `back_region_provided` | boolean | A 是否提交了非空 `back_point`；`false` 表示后排比例不是区域实测值 |
| `duration_seconds` | number，`> 0` | ffprobe 读取的原视频真实时长，单位秒 |
| `sample_interval_seconds` | number，`> 0` | 本次学生抽帧的目标间隔，单位秒，不是接口处理耗时；当前默认 `10.0` |
| `frames` | object[] | 按时间升序返回的全图逐采样点计数；当前正常成功结果至少包含 0 秒采样点，长视频时数组可能较大 |
| `evidence` | object[] | 已筛选并持久化的学生代表帧；没有入选图片时为 `[]` |

`frames` 元素字段：

| 子字段 | 类型/取值 | 含义 |
|---|---|---|
| `timestamp_seconds` | number，`>= 0` | 采样点相对视频起点的秒数，不是服务器墙钟时间 |
| `detected_total` | integer，`>= 0` | 当前学生画面中检测到的人员目标数，来源于 VBas `ObjectType=100`；模型不负责确认人员身份 |
| `stable_person_count` | integer，`>= 0` | 当前帧检测到的人脸数，可作为抬头人数，来源于 `ObjectType=101`；不是稳定跟踪 ID 数 |
| `phone_count` | integer，`>= 0` | 当前帧检测到的玩手机人数，来源于 `ObjectType=201` |
| `sleep_count` | integer，`>= 0` | 当前帧检测到的睡觉人数，来源于 `ObjectType=202` |
| `read_count` | integer，`>= 0` | 当前帧检测到的阅读人数，来源于 `ObjectType=205` |

成功结果中的每个 `frames` 元素固定包含上述五类计数；VBas 未返回某类时平台补 `0`。VBas 虽还
支持学生举手和站立检测，但当前离线学生结果不输出 `hand_count` 或 `standing_count`。各行为计数
不是互斥集合，A 不应把 `phone_count`、`sleep_count`、`read_count` 相加后解释为行为学生总数。
`frames` 保留人数为 0 的采样点，但顶层两个人数中位数只使用 `detected_total > 0` 的帧；偶数个
有效帧的中位数可能以 `.5` 结尾，因此顶层人数是 number，不保证为整数。

有非空区域时，`front_occupancy_ratio` 或 `back_occupancy_ratio` 的计算过程是：先在每个
`detected_total > 0` 的全图采样点计算“对应区域人脸/抬头人数 / 同时刻全图检测总人数”，再对逐帧
比例取中位数并保留最多 6 位小数。它不是“区域人数中位数 / 全图人数中位数”，也不是区域座位容量
使用率；提供区域但没有有效人数帧时返回 `0.0`。

未提供某区域时，对应 `*_region_provided=false`，比例仍不会为 null。当前实现首次在前排
`0.10-0.15`、后排 `0.25-0.40` 范围内生成并最多保留 6 位小数的兜底值，再按当前课程的学生行为
任务记录和前/后排指标分别持久化复用；它只是展示兜底，不能解释为模型实测结果。

`evidence` 元素字段：

| 子字段 | 类型/取值 | 含义 |
|---|---|---|
| `category` | string | 当前可能为 `student_head_up`、`student_reading`、`student_sleeping`、`student_phone_use` |
| `capture_second` | number，`>= 0` | 代表帧在视频中的采样时刻，单位秒 |
| `confidence` | number，`0-1` | 该类别人数除以同帧 `detected_total` 后限制到 `0-1`；它不是 VBas 检测框置信度 |
| `path` | string | 已复制到配置结果根目录下的持久图片绝对路径；标准部署为 `/data/result/{task_id}/vision/...`，不是 HTTP URL |

默认同类证据 30 秒内只保留更高 `confidence` 的帧，每类最多 3 张、单任务最多 20 张；这些是部署
参数，`evidence=[]` 只表示没有入选或持久化的学生证据图，不表示分析失败。所有采样帧人数均为 0 时，
两个顶层人数和到课率为 `0.0`，已提供区域的比例为 `0.0`，未提供区域仍返回持久兜底值，逐帧零计数
中的这些 `detected_total=0` 采样点仍保留在 `frames` 中。`frames=[]` 不是当前正常成功形态；抽帧或
任一 VBas 单帧响应失败会使节点失败，不会用 null 字段冒充成功结果。

上述字段都位于节点 `result` 内。节点外层 `count` 是持久证据图片数量，包括 `0`；只有证据非空时
才返回外层 `path`，其值是该任务的视觉结果目录。

平台默认使用：

```text
/data/course/{task_id}   临时下载视频、WAV、普通抽帧；满足受控清理条件后可清理
/data/result/{task_id}   PPT 切片和精选视觉证据；正常流程长期保留
```

path 是平台服务器或共享文件系统中的绝对路径。A 如果需要直接读取 PPT 图片，必须和平台共享
同一挂载，并保持路径一致；没有共享挂载时不能把该值拼成 URL。平台不会把 path 自动转换为下载
链接，也不会把视频或图片 Base64 放进课程查询响应。

A 不得在看到任务终态后自行删除 /data/course。平台的安全清理条件至少包括所有已请求任务类型和
节点均处于终态、结构化结果已落库、声明的持久文件真实存在且位于对应 /data/result/{task_id}
边界内，并且没有执行仍在读取临时文件。当前自动触发清理的运行时入口尚未形成对 A 的稳定合同，
因此 /data/course 既不能被 A 当作长期存储，也不能假设任务结束后一定立即消失。

### 6.6 离线提交与查询重试矩阵

| 情况 | 建议 |
|---|---|
| HTTP 超时，无法确认是否接收 | 使用相同 task_id 和任务类型重试；随后查询确认 |
| HTTP 200、code=40001 | 修正字段，不重试原正文 |
| HTTP 200、code=40401 | 检查课程 ID，不继续轮询 |
| HTTP 200、code=50000 | 有界退避；不要并发制造大量相同提交 |
| 已接收后状态 30 | 继续轮询；这是等待算子容量，不是提交失败；真正的已排队是状态 40 |
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

已进入路由且被网关处理的参数、容量和算子调用错误，当前通常仍以 HTTP 200 返回，错误由
外层 code 表示。但非法 JSON、FastAPI 在路由外产生的协议/校验错误，以及反向代理、负载均衡器
和网络层错误仍可能返回非 200。A 必须先检查 HTTP 状态和响应是否为可解析 JSON，再检查外层
code 与算子内层状态，不得把“HTTP 200”单独当成业务成功。

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
- Points、frame_id、frame_index、timestamp_seconds 可随请求透传；Points 是由整型 X/Y 点组成的
  多边形，VBas 只对区域内画面推理。
- 学生阈值覆盖字段名固定为 Student_Thresd，可包含 phone、hand、sleep、stand、read，
  每项范围是 0-1。
- 教师阈值覆盖字段名固定为 Teacher_Behavior_Thresd，可包含 sit、stand、bbwriting、teach。
  当前教师请求模型未强制限定数值范围，A 仍应只传 0-1，避免无意义阈值。
- 只有教师请求支持 ReturnHeadPose JSON 布尔值。它为 true 且 VBas 部署同时启用头部姿态模型时，
  每图可返回 HeadPoseResult；服务端未启用时该字段会省略。
- 多图片请求整体选择一个 VBas 实例，不会拆到多个实例；返回顺序保留。

ResultList 中的 ObjectType 是稳定数值类型，ObjectCount 是该类数量，ObjectPostList 是可选的目标框数组：

| stream_type | ObjectType | 含义 |
|---|---:|---|
| student | 100 | 检测人数 |
| student | 101 | 脸/抬头人数；离线视觉聚合将它作为稳定人数 |
| student | 201 | 使用手机 |
| student | 202 | 睡觉 |
| student | 203 | 举手 |
| student | 204 | 站立 |
| student | 205 | 阅读 |
| teacher | 100 | 讲台主体/讲台有人 |
| teacher | 201 | 坐 |
| teacher | 202 | 站立 |
| teacher | 203 | 板书 |
| teacher | 204 | 讲授 |

ObjectPostList 中的坐标字段是 LeftTopX、LeftTopY、RightBtmX、RightBtmY，Confidence 可选；
教师坐/站结果还可带 SuspectedSitting 或 PostureFallback。ObjectCount=0 时 ObjectPostList 通常为 null。

典型响应：

```json
{
  "code": 0,
  "message": "VBas 在线分析完成",
  "data": {
    "StatusObject": {"StatusString": "success", "StatusCode": 0},
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
当前 VBas 实现在单图处理抛异常时也可能直接返回非 2xx，网关会将它折叠为外层 code=50000；
这种情况没有可用 DataList，A 只能把整次结果视为未知/失败并做有界重试。

### 7.3 FaceRec 人脸识别

```http
POST /api/online/face/recognize
Content-Type: application/json
```

请求：

```json
{
  "photo": "data:image/jpeg;base64,/9j/4AAQ...",
  "targets": ["T001", "T002"],
  "threshold": 0.4
}
```

| 字段 | 必填 | 说明 |
|---|---:|---|
| photo | 是 | 单张图片的严格 Base64 或图片 Data URL |
| targets | 否 | 人物编号字符串数组；不是人物 ID 数组 |
| threshold | 否 | 相似度阈值，建议在 0-1 范围；省略时使用 FaceRec 部署配置 |

当前 FaceRec 会始终执行全库匹配：返回全库中达到 threshold 的前三名；传 targets 时还会对这些
编号按 threshold/2 做附加匹配并合并结果。因此 targets 不是“只在指定人员中查找”的硬过滤条件，
A 必须通过 match[i].is_target 判断结果是否来自目标列表。

匹配成功示例：

```json
{
  "code": 0,
  "message": "人脸对比完成",
  "data": {
    "status_code": 200,
    "message": "识别成功",
    "data": {
      "has_face": true,
      "bbox": {"x": 320, "y": 120, "w": 180, "h": 180},
      "threshold": 0.4,
      "match": [
        {
          "id": "66b...",
          "name": "张老师",
          "number": "T001",
          "similarity": "92.10%",
          "is_target": true
        }
      ],
      "message": "匹配成功"
    }
  }
}
```

FaceRec 的 data.status_code 是第二层业务状态，常用值如下：

| status_code | 含义 | A 服务处理 |
|---:|---|---|
| 200 | 检测到人脸且匹配成功 | 读取 data.data.match；它是数组 |
| 201 | 图片中没有检测到人脸 | 正常业务未识别，不按网关故障重试 |
| 202 | 人脸尺寸过小 | 提示重新采集更清晰、更近的图片 |
| 251 | 人物库为空 | 先完成人物录入 |
| 252 | 有人脸但相似度低于阈值 | 正常未匹配，match 为 null |
| 400-403 | 请求、Base64 或图片数据错误 | 修正图片或字段 |
| 500-503 | 检测、特征、数据库或文件保存异常 | 结合 message 和 data 判断，告警或有限重试 |

外层 code=0、内层 status_code=201/202/251/252 都不是 HTTP 调用失败。A 不应只看到外层成功就
读取 match[0]，也不应把“未检测到人脸”或“未匹配”无限重试成流量风暴。

### 7.4 FaceRec 人物库管理

人物库管理通过 Online Gateway 暴露五条路由：

| 操作 | 方法与路径 | 请求 |
|---|---|---|
| 单人录入或更新 | POST /api/online/face/persons | photo、name、number |
| 批量录入或更新 | POST /api/online/face/persons/batch | persons 数组 |
| 分页列表 | GET /api/online/face/persons?skip=0&limit=100 | 查询参数 |
| 条件搜索 | POST /api/online/face/persons/search | name 和/或 number |
| 删除 | DELETE /api/online/face/persons/delete | id、name、number 三选一 |

单人录入：

```json
{
  "photo": "data:image/jpeg;base64,/9j/4AAQ...",
  "name": "张老师",
  "number": "T001"
}
```

number 已存在时不是“重复创建失败”，而是更新该人物的姓名和人脸特征；不存在时才创建。成功的
内层 data 通常包含 id、name、number、photo_path 和 tip。photo_path 是 FaceRec 服务器内部
路径，不是 A 可下载的 URL。

批量录入：

```json
{
  "persons": [
    {
      "photo": "data:image/jpeg;base64,/9j/4AAQ...",
      "name": "张老师",
      "number": "T001"
    },
    {
      "photo": "data:image/jpeg;base64,/9j/4AAQ...",
      "name": "李老师",
      "number": "T002"
    }
  ]
}
```

批量返回的内层 status_code=200 表示全部成功，207 表示部分成功，400 表示全部失败。出现 207 时
读取 data.data.success_count、failed_count、failed_numbers、failed_details 和 persons，只补偿
失败项，不要重发已经成功的整批。

列表和搜索：

```http
GET /api/online/face/persons?skip=0&limit=100
POST /api/online/face/persons/search
```

```json
{
  "name": "张",
  "number": "T001"
}
```

- skip 建议为非负整数，limit 建议为正整数；默认分别为 0 和 100。
- 只传 name 时做姓名模糊搜索；只传 number 时做编号精确搜索；两者同时传时使用 AND 条件。
- 搜索至少提供一个字段，当前最多返回 20 条；搜索无结果时仍可返回内层 status_code=200、
  persons=[]。
- 人物库列表为空时当前返回内层 status_code=404，这与搜索无结果的语义不同。

删除请求：

```http
DELETE /api/online/face/persons/delete
Content-Type: application/json
```

```json
{
  "number": "T001"
}
```

删除支持 id 精确匹配、number 精确匹配和 name 模糊匹配。name 可能一次删除多人；如果正文同时
提供多个选择器，当前优先级是 name、number、id。A 应每次只发送一个选择器，优先使用 id 或
number，且必须确认所使用的 HTTP 客户端和反向代理支持 DELETE 请求体。成功后检查
data.data.deleted_count，未找到返回内层 status_code=404。

人物管理调用固定配置的 FaceRec 管理地址，不申请平台容量租约，也不经过四条在线推理路由的
72/50 MiB 图片限制中间件。A 仍应主动限制照片大小，避免超大正文拖垮管理接口。人物录入、批量
录入和删除都是有副作用操作：超时后结果未知时先列表或搜索确认，不要盲目自动重试；尤其是删除
成功后的重试会变成 404，批量录入也可能第一次已经部分生效。

人物管理同样使用双层响应：

```json
{
  "code": 0,
  "message": "人物录入完成",
  "data": {
    "status_code": 200,
    "message": "人物特征创建成功",
    "data": {
      "id": "66b...",
      "name": "张老师",
      "number": "T001",
      "photo_path": null,
      "tip": ""
    }
  }
}
```

必须检查内层 data.status_code。特别是内层 503 在人物录入中可能表示“特征已保存但人脸图片文件
保存失败”，它不同于平台外层 code=50301 的“没有算子容量”，不能把两者混为一谈。

### 7.5 ScreenDet 图像质量聚合检测

```http
POST /api/online/image-quality/detect
Content-Type: application/json
```

请求：

```json
{
  "image": "data:image/jpeg;base64,/9j/4AAQ...",
  "include": ["tilt", "screen", "quality_abnormal", "occlusion"],
  "tilt_threshold": 1.5,
  "screen_conf": 0.25,
  "screen_iou": 0.45,
  "occlusion_threshold": 0.25,
  "occlusion_area_ratio": 0.2
}
```

| 字段 | 必填 | 约束 |
|---|---:|---|
| image | 是 | 单张图片 Base64 或图片 Data URL |
| include | 否 | tilt、screen、quality_abnormal、occlusion 的子集；省略时使用部署默认模块 |
| tilt_threshold | 否 | 大于等于 0 |
| screen_conf | 否 | 0-1 |
| screen_iou | 否 | 0-1 |
| occlusion_threshold | 否 | 0-1 |
| occlusion_area_ratio | 否 | 0-1 |

include 中的重复模块会去重并保持首次顺序；空数组会执行零个模块，通常没有业务意义。网关只前置
校验 image，其他字段由 ScreenDet 算子校验。因此非法 include 或阈值当前可能被网关折叠为外层
code=50000，而不是 40001，A 应按上表在调用前完成校验。

响应主体示例：

```json
{
  "code": 0,
  "message": "图像质量检测完成",
  "data": {
    "code": 200,
    "msg": "检测完成",
    "start_time": "1787623200000",
    "end_time": "1787623200120",
    "cost_ms": 120.0,
    "executed_modules": ["tilt", "screen", "quality_abnormal", "occlusion"],
    "failed_modules": [],
    "effective_params": {
      "tilt_threshold": 1.5,
      "screen_conf": 0.25,
      "screen_iou": 0.45,
      "occlusion_threshold": 0.25,
      "occlusion_area_ratio": 0.2,
      "include": ["tilt", "screen", "quality_abnormal", "occlusion"],
      "device": "cuda:0"
    },
    "problem_types": ["tilt"],
    "tilt": {
      "code": 200,
      "msg": "检测完成",
      "cost_ms": 12.3,
      "result": {"is_tilted": true, "angle": 3.2, "cost_ms": 12.3}
    },
    "screen": {
      "code": 200,
      "msg": "检测完成",
      "cost_ms": 22.5,
      "primary": {
        "label": 3,
        "confidence": 0.94,
        "box": [120.0, 80.0, 1800.0, 1000.0]
      },
      "detections": [
        {
          "label": 3,
          "confidence": 0.94,
          "box": [120.0, 80.0, 1800.0, 1000.0]
        }
      ]
    },
    "quality_abnormal": {
      "code": 200,
      "msg": "检测完成",
      "cost_ms": 31.2,
      "is_abnormal": false,
      "abnormal_types": [],
      "results": [],
      "message": "图像质量正常"
    },
    "occlusion": {
      "code": 200,
      "msg": "检测完成",
      "cost_ms": 45.1,
      "is_occluded": false,
      "occlusion_area_ratio": 0.03,
      "score": 0.1,
      "threshold": 0.25,
      "area_ratio": 0.2,
      "message": "未检测到遮挡"
    }
  }
}
```

判定顺序：

1. 检查外层 code；非 0 表示网关校验、容量或算子调用失败。
2. 外层成功后检查 data.code，当前成功或部分模块失败通常均为 200。
3. 检查 failed_modules 和每个已执行模块的 code。failed_modules 非空表示执行层部分失败。
4. problem_types 表示“成功执行后检测到的问题类型”，不是执行失败列表。非空是有效业务结果，
   不应因检测出倾斜、遮挡等问题而自动重试。
5. 以 effective_params 作为本次实际阈值和模块集合，不要仅依赖请求值或部署默认值。

### 7.6 OCR 在线识别的内层结果

第 2 节定义了请求和基础响应。本节补充 A 的成功判定与二次解析规则。一次在线请求只适配为 OCR
算子的单元素 key/value 数组：

```text
外层 data
├── err_no
├── err_msg
├── key[0]                 A 提供或网关生成的 image_id
├── value[0]               JSON 字符串，需要再次 JSON.parse
└── formula_results[0]     可选的公式识别状态和结果
```

普通 OCR 的 value[0] 二次解析后示例：

```json
[
  {
    "text": "函数 y=x²",
    "confidence": 0.987,
    "text_region": [[10, 20], [200, 20], [200, 60], [10, 60]]
  }
]
```

A 必须依次检查：

- 外层 code==0；
- data.err_no==0；
- data.key 恰有一项；请求传入 image_id 时 key[0] 必须与它一致，省略 image_id 时以
  key[0] 作为网关生成的本次图片标识并保留；
- data.value 恰有一项，且 value[0] 能解析为 JSON 数组。

当 enable_formula=true 时，还需要逐项检查 formula_results：

```json
[
  {
    "image_id": "frame-001",
    "status": "success",
    "message": "",
    "formulas": [
      {
        "latex": "\\frac{a}{b}",
        "formula_region": [[10, 20], [100, 20], [100, 50], [10, 50]],
        "detection_confidence": 0.96
      }
    ]
  }
]
```

公式能力需要请求开关和 OCR 服务端总开关同时开启。status=disabled 表示服务端公式能力未启用；
status=error 表示该图片公式识别失败。公式识别失败可以与 err_no=0、普通 OCR 成功同时出现，
因此不能只检查 err_no。enable_formula=false 时 formula_results 通常为空数组。

可能出现 HTTP 200、外层 code=0、data.err_no!=0；此时算子业务失败，错误原因读取 err_msg。
image_id 只用于关联，不提供服务端幂等去重；重试会产生一次新的 OCR 推理和容量租约。

### 7.7 在线请求的超时与重试

在线 JSON 推理的网关绝对硬超时默认 600 秒，属于可部署配置。A 的客户端和反向代理超时应与
双方确认的业务 SLA 对齐；如果外层代理先超时，A 无法仅凭本次连接判断算子是否已经完成。

| 情况 | 是否建议自动重试 | 处理 |
|---|---|---|
| HTTP 200、外层 code=40001 | 否 | 修正字段、Base64 或大小 |
| HTTP 200、外层 code=50301 | 是，有界 | 指数退避并加入抖动；平台不会排队；持续出现时按租约/Control 故障告警 |
| HTTP 200、外层 code=50000 | 仅无副作用推理可有限重试 | 保留 X-Trace-ID；人物录入/删除先查询确认 |
| 外层 code=0、FaceRec 201/202/251/252 | 否 | 按未检测、过小、库空或未匹配处理 |
| 外层 code=0、ScreenDet problem_types 非空 | 否 | 这是检测到问题的成功结果 |
| 外层 code=0、ScreenDet failed_modules 非空 | 视业务而定 | 只补偿失败模块所需的请求 |
| 外层 code=0、OCR err_no 非 0 | 可有限 | 记录 err_msg；同图重试仍会重新推理 |
| 网络超时或连接中断 | 区分接口 | 识别类可有限重试；人物库变更先查后补偿 |

所有重试都应设置最大次数、总时间预算和抖动，不要并发重发同一大图。平台在线接口没有请求级
幂等键；X-Trace-ID 用于追踪，不会阻止重复执行。

## 8. 实时 ASR WebSocket 详细合同

### 8.1 入口和会话边界

宿主机或跨机器调用：

```text
ws://<online-gateway-host>:18103/api/online/asr/stream
```

同一 Docker network 内：

```text
ws://online-gateway-service:8001/api/online/asr/stream
```

经 TLS 反向代理时使用 wss://。建议握手携带 A 生成的 X-Trace-ID；WebSocket 路径会用它关联
内部租约，但当前没有“由平台生成并在握手后回传新 trace_id”的北向合同。

网关会先接受 WebSocket 握手，再申请一个 asr_online 容量租约。因此“握手成功”不等于已经取得
容量。取得容量后，一个会话固定连接同一 ASR 实例并持续续租；重连会创建全新会话、选择新实例，
不会恢复上次识别缓存。实时结果不写入离线课程任务库，也不能替代 ASR_TRANSCRIPTION。

### 8.2 上行音频格式

| 属性 | 要求 |
|---|---|
| WebSocket 消息类型 | binary |
| 音频内容 | 原始 PCM，不带 WAV/RIFF 文件头 |
| 采样率 | 16000 Hz |
| 声道 | 单声道 |
| 采样格式 | signed int16 little-endian，即 s16le |
| 推荐每帧样本数 | 7680 |
| 推荐每帧字节数 | 15360 |
| 推荐发送节奏 | 每约 480 ms 发送一帧 |

当前 ASR 实现把每个 WebSocket binary 消息固定计作 0.48 秒，不根据实际字节数计算 bg/ed。其他
帧长虽然未必在入口立即拒绝，但会导致时间戳漂移；奇数字节可能直接中断会话，错误采样率、
声道或编码即使是偶数字节也通常只会被当作 PCM16 误解析并产生错误识别，不会被可靠拒绝。A 应固定
使用 7680 个 int16 样本，末帧不足时补零。

音频转码示例：

```bash
ffmpeg -i input.mp4 -f s16le -acodec pcm_s16le -ac 1 -ar 16000 output.pcm
```

不支持 JSON start/end、文本音频帧或显式 EOS 消息。网关虽然能转发 WebSocket 文本消息，但
ASR 算子只读取 binary；发送文本属于协议错误，通常以 1011 结束。

### 8.3 下行增量结果

ASR 每处理一个音频帧返回一个 JSON 文本消息：

```json
{
  "key": "rand_key_a1B2c3D4e5F6",
  "text": "今天我们学习函数。",
  "finished": false,
  "bg": 0.48,
  "ed": 0.96
}
```

| 字段 | 类型 | 语义 |
|---|---|---|
| key | string | 单条响应的随机标识，不是稳定会话 ID |
| text | string | 当前语句截至本帧的累计识别文本，允许为空或被重新加标点 |
| finished | boolean | 当前语句是否结束；true 不会关闭 WebSocket |
| bg | number | 当前实现按 0.48 秒固定块时钟生成的起点参考值；不是可靠的真实语句起点 |
| ed | number | 当前实现按固定块时钟生成的终点参考值 |

finished=false 时，A 应替换当前临时字幕，不要把每次 text 直接追加，否则累计文本会重复。
finished=true 时，把 text 固化为一条语句并清空本地临时字幕，连接继续接收下一句。

当模型连续两帧返回空文本时，实现会进入类似“静音分句”的分支；只有当缓冲区内已有文本时才返回
finished=true，纯空输入仍会返回 finished=false。单句过长也会触发 finished=true。这是模型文本
输出启发式，不是对音频能量的严格 VAD 承诺。客户端主动断开不会触发最终模型 flush，尚未收到
finished=true 的缓冲文本可能丢失。停止采集时应尽量继续发送短暂静音并等待最后的终态语句，
但协议不保证固定数量的全零帧一定触发终态，也没有可发送的“强制提交最后一句”控制消息。

客户端逻辑示意：

```text
connect
for each 15360-byte PCM frame:
    send binary frame
    receive text message
    if message.code == 50301: stop and back off
    else if message.finished: commit message.text as one sentence
    else: replace current partial subtitle with message.text
send trailing silence when possible
wait briefly for finished=true
close normally
```

### 8.4 容量、关闭码和重连

| 情况 | 网关行为 | A 服务处理 |
|---|---|---|
| 租约初次申请失败或会话续租异常 | 先发 code=50301 JSON，再以 1013 关闭 | 停止发送，指数退避后新建会话 |
| 上游连接、算子、代理异常 | 以 1011 关闭 | 保存 trace ID；有界重连，接受未完成语句丢失 |
| 达到会话绝对时长上限 | 当前以 1011 关闭 | 在上限前主动平滑换会话 |
| A 主动断开 | 停止代理并释放租约 | 不能期待服务器补发最后一句 |

无容量消息：

```json
{
  "code": 50301,
  "message": "暂无可用实时 ASR 算子容量",
  "data": null
}
```

当前网关到 ASR 算子的实现内部固定使用 10 秒连接超时、20 秒 WebSocket ping 间隔和 20 秒 pong
超时；它们不是 A 到网关的心跳协议，当前也不能通过同名 TOML 字段覆盖。单会话绝对上限的可部署
默认值是 14400 秒（4 小时）。内部租约 TTL 默认 3600 秒且会自动续租，不要求 A 每小时重连。
对 A 而言，50301 同时覆盖真实容量不足、Control 租约调用/解析失败和续租异常，不能仅凭该码断定底层
原因。实际可用会话数取决于实例注册、健康、排空和容量状态。

A 的重连必须创建新的 WebSocket，不能在已收到 1011/1013 的对象上继续发送。建议采用指数退避、
随机抖动和最大重连预算；如果需要无缝字幕，由 A 自己保留已 finished 的语句并标记跨会话断点。

## 9. A 服务部署与文件访问边界

### 9.1 先给结论

A 服务（上游调用方）通过 HTTP/HTTPS 或 WebSocket 使用平台北向接口。只提交视频 URL、消费结构化
JSON、发送在线 Base64 图片或实时 ASR 音频字节时，A 不需要挂载 NFS 或其他共享文件系统。

平台内部是否采用单机、NFS、CephFS 或其他多机方案，由平台和运维团队负责部署与验收。A 服务只需
依据交付的北向地址调用，并确认媒体可下载、业务状态可查询以及所需结果能够读取。

### 9.2 A 侧场景与共享存储

| A 侧场景 | 传输/读取方式 | A 是否需要共享存储 |
|---|---|---|
| 提交离线课程任务 | 向 Control 提交 HTTP/HTTPS 视频 URL | 否；但 URL 必须能从平台 Orchestrator 所在网络访问 |
| 查询 OCR、ASR、教师/学生统计 | Control 返回结构化 JSON | 否 |
| 在线 VBas、FaceRec、ScreenDet、OCR | 向 Online Gateway 发送 Base64 JSON | 否 |
| 实时 ASR | 向 Online Gateway 发送 PCM WebSocket 字节 | 否 |
| 直接读取 PPT 切片或视觉证据文件 | 按响应中的绝对 `path` 读取 | 是；只读共享同一 `/data/result` |

### 9.3 媒体 URL 要求

- 视频 URL 必须能从平台 Orchestrator 所在网络直接访问，不能只在 A 服务所在机器可访问。
- URL 必须是可直接下载的 HTTP/HTTPS 地址；当前下载器不跟随重定向。
- 签名 URL 或临时授权地址的有效期必须覆盖任务排队和实际下载时间。
- 平台不会替 A 附加自定义下载请求头；需要鉴权时应提供双方约定的可直接访问地址。
- 提交返回 `code=0` 只表示任务事实已接收，不表示视频已经下载成功；下载失败会在后续查询状态和
  节点 `reason` 中体现。

### 9.4 结果文件 path

- A 只消费课程查询中的结构化 JSON 时，不需要共享 `/data/result`。
- A 只有需要直接读取 PPT 切片或视觉 `evidence` 文件时，才需要将平台使用的同一份
  `/data/result` 以只读方式挂载，并保证 A 进程看到的绝对路径与响应 `path` 完全一致。
- A 不应挂载或依赖 `/data/course`；它是平台内部临时工作区。
- 当前没有面向 A 的结果文件下载接口、对象存储 URI 或签名下载 URL。没有共享 `/data/result`
  时，响应中的 `path` 只能作为平台内部文件引用，不能拼接成 HTTP URL。
- `/data/result` 不承诺永久保存。双方应在上线前确认结果保留周期、归档、备份和容量告警责任。

### 9.5 A 侧上线验收

1. 使用平台交付的实际域名、端口、TLS 和鉴权信息完成 Control 与 Online Gateway 连通测试。
2. 使用真实视频 URL 提交离线任务，确认平台能够下载，并能通过查询看到成功或明确的失败原因。
3. 验证 A 只消费 JSON 时不依赖共享目录，在线图片和实时 ASR 也不依赖文件路径。
4. A 需要直接读取文件时，验证 `/data/result` 为只读挂载、响应 `path` 可读且 A 无法写入或删除。
5. 平台内部多机部署由平台/运维团队验收并向 A 交付结论；A 只验证北向地址、媒体下载和业务响应。

## 10. A 服务实现与联调清单

### 10.1 北向接口总表

| 场景 | 方法 | 路径 | 服务 |
|---|---|---|---|
| 提交离线课程任务 | POST | /api/course-jobs | Control Service |
| 查询课程完整状态 | GET | /api/course-jobs/{task_id} | Control Service |
| 在线 VBas | POST | /api/online/vbas/analyze | Online Gateway |
| 在线人脸识别 | POST | /api/online/face/recognize | Online Gateway |
| 单人人物录入/更新 | POST | /api/online/face/persons | Online Gateway |
| 批量人物录入/更新 | POST | /api/online/face/persons/batch | Online Gateway |
| 人物列表 | GET | /api/online/face/persons | Online Gateway |
| 人物搜索 | POST | /api/online/face/persons/search | Online Gateway |
| 人物删除 | DELETE | /api/online/face/persons/delete | Online Gateway |
| 图像质量检测 | POST | /api/online/image-quality/detect | Online Gateway |
| 在线 OCR | POST | /api/online/ocr/recognize | Online Gateway |
| 实时 ASR | WebSocket | /api/online/asr/stream | Online Gateway |

A 不得直接调用 /internal、/ops、算子原始路由、数据库、Redis 或 Kafka。宿主机默认端口是 Control
18100、Online Gateway 18103；同一 Docker network 中 Online Gateway 使用容器端口 8001。
实际域名、TLS、鉴权和端口以部署方交付为准。

### 10.2 统一响应判定流程

```text
收到 HTTP 响应
  ├─ 非 2xx / 非 JSON
  │    └─ 记录 trace_id，按接口副作用决定查询、补偿或重试
  └─ 2xx + JSON
       ├─ 外层 code != 0
       │    └─ 按 40001 / 40401 / 50000 / 50301 处理
       └─ 外层 code == 0
            ├─ 离线提交：读取 created，随后查询
            ├─ 离线查询：按 task status + nodes status 处理
            ├─ VBas：检查两层 StatusCode
            ├─ FaceRec：检查 data.status_code
            ├─ ScreenDet：检查 data.code + failed_modules
            └─ OCR：检查 data.err_no，并解析 value[0]
```

不要把不同算子的内层 code/status_code/err_no 统一成“只要等于 0 就成功”。FaceRec 成功值是
200，ScreenDet 成功值通常是 200，OCR 才以 err_no=0 表示成功。


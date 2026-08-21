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

# 屏幕倾斜 + 屏幕类型检测服务 — 接口文档

| 项目 | 说明 |
|------|------|
| 服务名 | tilt-detection-service |
| 版本 | v1.0.0 |
| 默认端口 | 8880 |
| 基础地址 | `http://<host>:8880` |
| 路由形式 | 所有接口均使用无前缀根路径 |
| 配置来源 | 根目录 `config.toml`（Docker 建议挂载） |

---

## 目录

1. [通用约定](#通用约定)
2. [接口列表](#接口列表)
3. [接口明细表（填表格式）](#接口明细表填表格式)
4. [错误码汇总](#错误码汇总)
5. [附录：label 枚举与配置项](#附录label-枚举与配置项)

---

## 通用约定

### 请求头

| Header | 说明 |
|--------|------|
| `Content-Type` | `detect_tilt`：支持 `application/json` 或 `text/plain`；其余 POST 检测接口必须 `application/json` |
| `X-Request-ID` | 可选；未传时服务端自动生成，并在响应头回传 |

### 时间字段

`start_time`、`end_time` 为 **北京时间** 对应的毫秒时间戳字符串。

### 图片入参

- 编码：Base64 字符串
- 支持 `data:image/jpeg;base64,` 等 data URL 前缀（自动剥离）
- 单张解码后体积上限：默认 **10MB**（`config.toml` → `[runtime].max_image_bytes`）

### 成功 / 失败响应

| HTTP 状态码 | Body 结构 |
|-------------|-----------|
| 200 | 各接口业务 JSON（含 `code: 200`） |
| 400 | `{"code": 400, "msg": "..."}` |
| 500 | `{"code": 500, "msg": "..."}` |
| 503 | 仅 `/health`：YOLO 未就绪时 |

---

## 接口列表

| 序号 | 功能类型 | 方法 | URL | 说明 |
|------|---------|------|----------------|------|
| 1 | 服务信息 | GET | `/` | 返回服务名、版本、各接口路径 |
| 2 | 健康检查 | GET | `/health` | 运行状态、GPU、YOLO 预加载状态 |
| 3 | 倾斜检测 | POST | `/detect_tilt` | OpenCV CPU 线段角度检测 |
| 4 | 屏幕检测 | POST | `/detect_screen` | YOLO GPU 屏幕类型检测（支持多图） |
| 5 | **组合检测** | POST | `/detect_inspect` | 单图：倾斜 + 屏幕（推荐业务入口） |
| 6 | 画面异常检测 | POST | `/detect_quality_abnormal` | OpenCV CPU：虚焦、偏色、雪花噪点、花屏 |
| 7 | 镜头遮挡检测 | POST | `/detect_occlusion` | YOLO-seg：镜头近处遮挡与 mask 面积占比 |
| 8 | 全量聚合检测 | POST | `/detect_all` | 单图：倾斜 + 屏幕 + 画面异常 + 镜头遮挡 |
| 9 | 配置查询 | GET | `/config` | 返回当前配置快照 |
| 10 | 配置重载 | POST | `/config/reload` | 热重载部分配置（见说明） |

---

## 1. 服务信息

**URL：** `GET /`

**请求：** 无

**响应示例：**

```json
{
  "service": "tilt-detection-service",
  "version": "1.0.0",
  "health": "/health",
  "detect_tilt": "/detect_tilt",
  "detect_screen": "/detect_screen",
  "detect_inspect": "/detect_inspect",
  "detect_all": "/detect_all",
  "detect_quality_abnormal": "/detect_quality_abnormal",
  "detect_occlusion": "/detect_occlusion"
}
```

---

## 2. 健康检查

**URL：** `GET /health`

**请求：** 无

**响应字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | `success` / `not_ready` |
| ready | boolean | 是否可接业务流量 |
| elapsed_time | string | 运行时长，如 `0h 5m 12s` |
| total_requests | int | 累计请求数 |
| memory_mb | float | 进程内存（MB） |
| gpu | object | GPU 配置与解析后的 YOLO 设备 |
| screen_model | object | YOLO 模型加载与 warmup 状态 |

**screen_model 子字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| loaded | boolean | 权重是否已加载 |
| warmed_up | boolean | 是否已完成 GPU warmup |
| device | int/string | 实际推理设备 |
| device_name | string | GPU 名称（可选） |
| gpu_memory_mb | float | GPU 显存占用（MB，可选） |
| aattn_patched | int | 兼容补丁层数 |

**响应示例：**

```json
{
  "status": "success",
  "ready": true,
  "elapsed_time": "0h 2m 40s",
  "total_requests": 0,
  "memory_mb": 1451.01,
  "yolo": {
    "device": "cuda:0",
    "tilt_inference_device": "cpu",
    "yolo_device_resolved": "cuda:0",
    "cuda_visible_devices": null
  },
  "screen_model": {
    "loaded": true,
    "warmed_up": true,
    "device": "cuda:0",
    "device_name": "NVIDIA GeForce RTX 4090 D",
    "gpu_memory_mb": 512.0,
    "aattn_patched": 16
  },
  "occlusion_model": {
    "loaded": true,
    "warmed_up": true,
    "device": "cuda:0"
  }
}
```

**HTTP 503：** 任一YOLO模型尚未完成加载和预热时。

---

## 3. 倾斜检测

**URL：** `POST /detect_tilt`

**功能：** 基于 OpenCV 边缘与线段分析，估算画面倾斜角度（CPU）。

### 请求方式 A：JSON（推荐）

**Content-Type：** `application/json`

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| images | R | string | 图片 Base64 |
| image | O | string | 与 images 二选一 |
| tilt_threshold | O | float | 倾斜判定阈值（度），默认读 `config.toml` |

```json
{
  "images": "<base64>",
  "tilt_threshold": 1.5
}
```

### 请求方式 B：纯 Base64

**Content-Type：** `text/plain`

**Body：** 直接为 Base64 字符串（无 JSON 包裹）。此方式**不能**传 `tilt_threshold`，使用配置默认值。

### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| code | int | 200 表示成功 |
| start_time | string | 开始时间戳 |
| end_time | string | 结束时间戳 |
| msg | string | 结果描述 |
| tilt_threshold | float | 实际使用的阈值 |
| result | object | 检测结果 |
| result.is_tilted | boolean | `angle > tilt_threshold` 时为 true |
| result.angle | float | 倾斜角度（度，绝对值） |
| result.cost_ms | float | 耗时（毫秒） |

**成功示例：**

```json
{
  "code": 200,
  "start_time": "1753791280207",
  "end_time": "1753791280225",
  "msg": "检测完成",
  "tilt_threshold": 1.5,
  "result": {
    "is_tilted": true,
    "angle": 2.35,
    "cost_ms": 37.59
  }
}
```

**失败示例：**

```json
{
  "code": 400,
  "msg": "Missing field \"images\" or \"image\""
}
```

---

## 4. 屏幕类型检测（YOLO）

**URL：** `POST /detect_screen`

**功能：** 使用 `model/screen.pt` 检测屏幕类型（GPU）；仅返回 **label 0–3**。

**Content-Type：** `application/json`（必须）

### 请求参数

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| images | R | string / string[] | 单图 Base64 或多图数组 |
| conf | O | float | 置信度阈值，默认 0.25 |
| iou | O | float | NMS IoU，默认 0.45 |

**单图请求：**

```json
{
  "images": "<base64>",
  "conf": 0.25,
  "iou": 0.45
}
```

**多图请求：**

```json
{
  "images": ["<base64_1>", "<base64_2>"],
  "conf": 0.3
}
```

- 单次最多图片数：默认 **16**（`max_batch_size`）

### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| code | int | 200 |
| start_time | string | 开始时间戳 |
| end_time | string | 结束时间戳 |
| msg | string | 检测完成 / 未识别到有效类型 |
| conf | float | 实际使用的 conf |
| iou | float | 实际使用的 iou |
| total | int | 图片数量 |
| results | array | 每张图的结果 |
| results[].index | int | 序号，从 0 |
| results[].cost_ms | float | 单图耗时（ms） |
| results[].primary | object/null | 置信度最高的框 |
| results[].detections | array | 全部有效检测（label 0–3） |
| results[].primary.label | int | 0 蓝 / 1 黑 / 2 白 / 3 正常 |
| results[].primary.confidence | float | 0~1 |
| results[].primary.box | float[4] | `[x1, y1, x2, y2]` 左上 + 右下，像素 |

**成功示例：**

```json
{
  "code": 200,
  "start_time": "1753791280207",
  "end_time": "1753791280274",
  "msg": "检测完成",
  "conf": 0.25,
  "iou": 0.45,
  "total": 1,
  "results": [
    {
      "index": 0,
      "cost_ms": 48.6,
      "primary": {
        "label": 3,
        "confidence": 0.926,
        "box": [936.0, 55.0, 1697.0, 493.0]
      },
      "detections": [
        {
          "label": 3,
          "confidence": 0.926,
          "box": [936.0, 55.0, 1697.0, 493.0]
        }
      ]
    }
  ]
}
```

---

## 5. 组合检测（倾斜 + 屏幕）

**URL：** `POST /detect_inspect`

**功能：** 单张图一次完成倾斜检测（CPU）与幕布/屏幕类型检测（GPU）；Base64 只解码一次。

**Content-Type：** `application/json`（必须）

### 请求参数

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| image | R | string | 单张图片 Base64 |
| tilt_threshold | O | float | 倾斜阈值（度） |
| conf | O | float | YOLO 置信度 |
| iou | O | float | YOLO NMS IoU |

```json
{
  "image": "<base64>",
  "tilt_threshold": 1.5,
  "conf": 0.25,
  "iou": 0.45
}
```

### 响应约定

- **顶层 `code` 固定为 200**（HTTP 200）；子能力成败看 `tilt.code`、`screen.code`
- `tilt` / `screen` 各自含 `cost_ms`

### 响应示例（全部成功）

```json
{
  "code": 200,
  "start_time": "1753791280207",
  "end_time": "1753791280290",
  "msg": "检测完成",
  "tilt_threshold": 1.5,
  "conf": 0.25,
  "iou": 0.45,
  "tilt": {
    "code": 200,
    "msg": "检测完成",
    "cost_ms": 38.2,
    "result": {
      "is_tilted": true,
      "angle": 2.35,
      "cost_ms": 38.2
    }
  },
  "screen": {
    "code": 200,
    "msg": "检测完成",
    "cost_ms": 52.6,
    "primary": {
      "label": 3,
      "confidence": 0.926,
      "box": [936.0, 55.0, 1697.0, 493.0]
    },
    "detections": [...]
  }
}
```

### 无有效幕布/屏幕类型（与 `/detect_screen` 一致）

画面中 **未检出 label 0–3**（无幕布、仅其他类别被过滤、或置信度不足）时：

- `screen.code` 仍为 **200**
- `screen.primary` 为 **null**，`screen.detections` 为 **[]**
- `screen.msg` 为 **`检测完成，未识别到有效屏幕类型`**
- 顶层 `msg` 同步为该文案

> 这不等于倾斜失败，仅表示 YOLO 未给出蓝/黑/白/正常屏四类结果。

### 部分失败示例（顶层仍 code=200）

```json
{
  "code": 200,
  "msg": "倾斜检测完成，屏幕检测失败",
  "tilt": { "code": 200, "result": { "is_tilted": false, "angle": 0.52, "cost_ms": 40 } },
  "screen": { "code": 500, "msg": "Detection failed: ...", "primary": null, "detections": [] }
}
```

---

## 6. 画面异常检测

**URL：** `POST /detect_quality_abnormal`

**功能：** 使用 OpenCV 规则检测单张图片是否存在画面异常。异常类型支持多选，固定枚举为 `1=虚焦`、`2=偏色`、`3=雪花噪点`、`4=花屏`。

**Content-Type：** `application/json`

### 请求参数

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| image | R | string | 单张图片 Base64，支持 data URL 前缀 |

**请求示例：**

```json
{
  "image": "<base64>"
}
```

### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| code | int | 200 表示成功 |
| msg | string | 接口执行描述 |
| is_abnormal | boolean | 是否检测到任一画面异常 |
| abnormal_types | int[] | 命中的异常类型数组 |
| results | object[] | 仅包含命中类型的异常明细 |
| results[].type | int | 1=虚焦，2=偏色，3=雪花噪点，4=花屏 |
| results[].score | float | 异常分数，范围 0–1 |
| results[].message | string | 中文提示 |
| message | string | 总体中文提示 |

**成功示例：**

```json
{
  "code": 200,
  "msg": "检测完成",
  "is_abnormal": true,
  "abnormal_types": [1, 4],
  "results": [
    { "type": 1, "score": 0.76, "message": "疑似虚焦" },
    { "type": 4, "score": 0.71, "message": "疑似花屏" }
  ],
  "message": "检测到画面异常：虚焦、花屏"
}
```

**无异常示例：**

```json
{
  "code": 200,
  "msg": "检测完成",
  "is_abnormal": false,
  "abnormal_types": [],
  "results": [],
  "message": "未检测到画面异常"
}
```

**算法说明：**

- 检测顺序固定为：偏色 → 雪花噪点 → 虚焦 → 花屏。
- 花屏第一版为单图 OpenCV 规则，使用固定网格异常块、形态学连接和面积占比判断；明显花屏样例目标约 70% 准确率。
- `abnormal_types` 中出现的类型必须在 `results[].type` 中有对应明细；未命中类型不返回明细。

---

## 7. 镜头遮挡检测

**URL：** `POST /detect_occlusion`

**功能：** 检测镜头前或镜头不远处遮挡，并返回遮挡区域占整图面积比例。默认使用单类 YOLO-seg 模型 `model/occlusion.pt`，第一版不输出遮挡物枚举。

**Content-Type：** `application/json`

### 请求参数

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| image | R | string | 单张图片 Base64，支持 data URL 前缀 |
| threshold | O | float | YOLO 置信度阈值，范围 0–1；不传则使用 `config.toml` 默认值 |
| area_ratio | O | float | 遮挡面积占比判定阈值，范围 0–1；不传则使用 `config.toml` 默认值 |

**请求示例：**

```json
{
  "image": "<base64>",
  "threshold": 0.25,
  "area_ratio": 0.2
}
```

### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| code | int | 200 表示成功 |
| msg | string | 接口执行描述 |
| is_occluded | boolean | 是否检测到镜头近处遮挡 |
| occlusion_area_ratio | float | 遮挡面积占比，范围 0–1 |
| score | float | 检测分数，范围 0–1 |
| threshold | float | 本次实际使用的 YOLO 置信度阈值 |
| area_ratio | float | 本次实际使用的遮挡面积判定阈值 |
| message | string | 中文提示 |

**成功示例：**

```json
{
  "code": 200,
  "msg": "检测完成",
  "is_occluded": true,
  "occlusion_area_ratio": 0.2367,
  "score": 0.87,
  "threshold": 0.25,
  "area_ratio": 0.2,
  "message": "检测到镜头遮挡"
}
```

**无遮挡示例：**

```json
{
  "code": 200,
  "msg": "检测完成",
  "is_occluded": false,
  "occlusion_area_ratio": 0.0,
  "score": 0.0,
  "threshold": 0.25,
  "area_ratio": 0.2,
  "message": "未检测到镜头遮挡"
}
```

**算法说明：**

- YOLO-seg 后端使用 `model/occlusion.pt` 输出单类 `occlusion` 分割 mask。
- `threshold` 用于过滤低置信度 YOLO mask；`area_ratio` 用于最终遮挡判定。
- 多个有效 mask 会先合并并集，再计算 `occlusion_area_ratio = mask并集面积 / 整图面积`。
- 默认阈值来自 `config.toml`：`threshold=0.25`、`area_ratio=0.2`；请求入参传值时只覆盖本次检测。
- 在 1000 张已确认正常无遮挡图上，`threshold=0.25` 且 `area_ratio > 0.2` 的误报数为 0。
- 遮挡定义限定为镜头前或镜头不远处遮挡；教室内部普通人物、桌椅、黑板、投影屏或教学设备不属于遮挡。

---

## 8. 全量聚合检测

**URL：** `POST /detect_all`

**功能：** 单张图片一次完成倾斜检测、屏幕/幕布类型检测、画面异常检测和镜头遮挡检测。第一版固定串行执行，子模块失败不会阻断其他模块。

**Content-Type：** `application/json`

### 请求参数

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| image | R | string | 单张图片 Base64，支持 data URL 前缀 |
| tilt_threshold | O | float | 倾斜阈值，单位度；不传读 `[aggregate_detection].tilt_threshold` |
| screen_conf | O | float | 屏幕 YOLO 置信度，0–1；不传读 `[aggregate_detection].screen_conf` |
| screen_iou | O | float | 屏幕 YOLO NMS IoU，0–1；不传读 `[aggregate_detection].screen_iou` |
| occlusion_threshold | O | float | 遮挡 YOLO-seg 置信度，0–1；不传读 `[aggregate_detection].occlusion_threshold` |
| occlusion_area_ratio | O | float | 遮挡面积判定阈值，0–1；不传读 `[aggregate_detection].occlusion_area_ratio` |
| include | O | string[] | 执行模块，合法值：`tilt`、`screen`、`quality_abnormal`、`occlusion`；不传读 `[aggregate_detection].default_modules` |

`device` 只允许通过 `config.toml` 配置，不作为请求字段开放。

### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| code | int | 顶层请求执行状态，成功为 200 |
| msg | string | 顶层中文描述 |
| start_time / end_time | string | 北京时间毫秒时间戳字符串 |
| cost_ms | float | 聚合总耗时 |
| executed_modules | string[] | 本次实际执行模块 |
| failed_modules | string[] | 执行失败模块 |
| effective_params | object | 本次实际使用参数 |
| problem_types | string[] | 检测出业务问题的模块级枚举，合法值：`tilt`、`screen`、`quality_abnormal`、`occlusion` |
| tilt / screen / quality_abnormal / occlusion | object/null | 各模块结果块；未执行时为 `null` |

`problem_types=["tilt"]` 表示只有倾斜模块检测出业务问题；如果其他模块在 `executed_modules` 中且不在 `failed_modules` 中，则对应结果块应表达无异常。

### 响应示例

```json
{
  "code": 200,
  "msg": "检测完成",
  "start_time": "1753791280207",
  "end_time": "1753791281298",
  "cost_ms": 1091.4,
  "executed_modules": ["tilt", "screen", "quality_abnormal", "occlusion"],
  "failed_modules": [],
  "effective_params": {
    "tilt_threshold": 1.5,
    "screen_conf": 0.25,
    "screen_iou": 0.45,
    "occlusion_threshold": 0.25,
    "occlusion_area_ratio": 0.2,
    "include": ["tilt", "screen", "quality_abnormal", "occlusion"],
    "device": "cpu"
  },
  "problem_types": [],
  "tilt": {
    "code": 200,
    "msg": "检测完成",
    "cost_ms": 20.3,
    "result": { "is_tilted": false, "angle": 0.8, "cost_ms": 20.3 }
  },
  "screen": {
    "code": 200,
    "msg": "检测完成",
    "cost_ms": 80.1,
    "primary": { "label": 3, "confidence": 0.91, "box": [100.0, 50.0, 900.0, 500.0] },
    "detections": []
  },
  "quality_abnormal": {
    "code": 200,
    "msg": "检测完成",
    "cost_ms": 35.6,
    "is_abnormal": false,
    "abnormal_types": [],
    "results": [],
    "message": "未检测到画面异常"
  },
  "occlusion": {
    "code": 200,
    "msg": "检测完成",
    "cost_ms": 150.2,
    "is_occluded": false,
    "occlusion_area_ratio": 0.0,
    "score": 0.0,
    "threshold": 0.25,
    "area_ratio": 0.2,
    "message": "未检测到镜头遮挡"
  }
}
```

---

## 9. 配置查询

**URL：** `GET /config`

**请求：** 无

**响应：** 返回 `app`、`server`、`gpu`、`detection`、`screen_detection`、`quality_abnormal_detection`、`occlusion_detection`、`aggregate_detection`、`runtime` 配置对象（`config.toml` 快照）。

---

## 10. 配置重载

**URL：** `POST /config/reload`

**请求：** 无 Body

**响应示例：**

```json
{
  "code": 200,
  "msg": "Config reloaded",
  "detection": { "tilt_threshold": 1.5, "...": "..." },
  "screen_detection": {
    "weights_path": "model/screen.pt",
    "conf": 0.25,
    "iou": 0.45,
    "allowed_class_ids": [0, 1, 2, 3],
    "max_batch_size": 16
  },
  "quality_abnormal_detection": {
    "enabled": true,
    "analyze_max_side": 960
  },
  "occlusion_detection": {
    "enabled": true,
    "analyze_max_side": 960,
    "threshold": 0.25,
    "area_ratio": 0.2,
    "yolo_seg_weights_path": "model/occlusion.pt",
    "yolo_imgsz": 960,
    "yolo_retina_masks": true
  },
  "aggregate_detection": {
    "enabled": true,
    "default_modules": ["tilt", "screen", "quality_abnormal", "occlusion"],
    "tilt_threshold": 1.5,
    "screen_conf": 0.25,
    "screen_iou": 0.45,
    "occlusion_threshold": 0.25,
    "occlusion_area_ratio": 0.2
  }
}
```

**热重载范围：**

| 配置段 | 是否支持热重载 |
|--------|----------------|
| `[detection]` | ✅ |
| `[screen_detection]` 部分字段 | ✅ |
| `[quality_abnormal_detection]` | ✅ |
| `[occlusion_detection]` | ✅ |
| `[aggregate_detection]` | ✅ |
| `[yolo]`、`[model_protection]`、`[server]` | ❌ 需重启；热重载返回409 |

---

## 错误码汇总

| 错误码 | HTTP | 说明 | 适用接口 |
|--------|------|------|----------|
| 200 | 200 | 成功 | 全部业务接口 |
| 400 | 400 | 参数错误：缺字段、Base64 无效、图片过大、Content-Type 错误等 | 全部 POST 检测接口 |
| 500 | 500 | 服务内部错误 | 全部 POST 检测接口 |
| 503 | 503 | 服务未就绪（YOLO 未 preload/warmup） | health |

---

## 附录：label 枚举与配置项

### 屏幕检测 label

| label | 含义 |
|-------|------|
| 0 | blue-screen（蓝屏） |
| 1 | black-screen（黑屏） |
| 2 | white-screen（白屏） |
| 3 | normal-screen（正常屏） |

### 画面异常 type

| type | 含义 |
|------|------|
| 1 | 虚焦 |
| 2 | 偏色 |
| 3 | 雪花噪点 |
| 4 | 花屏 |

### 常用配置项（config.toml）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| server.port | 8880 | 监听端口 |
| server.workers | 1 | Uvicorn worker 数，单 GPU 建议 1 |
| yolo.device | cpu | 两个YOLO模型统一设备；支持cpu、mps、cuda:N |
| model_protection.enabled | false | 生产加密模型模式开关 |
| screen_detection.conf | 0.25 | 默认置信度 |
| screen_detection.iou | 0.45 | 默认 IoU |
| screen_detection.max_batch_size | 16 | 单次最大图片数 |
| detection.tilt_threshold | 1.5 | 倾斜判定阈值（度） |
| quality_abnormal_detection.analyze_max_side | 960 | 画面异常 OpenCV 分析最长边 |
| quality_abnormal_detection.glitch_min_area_ratio | 0.18 | 花屏异常区域最小占比 |
| occlusion_detection.threshold | 0.25 | YOLO 置信度阈值，请求 `threshold` 未传时使用 |
| occlusion_detection.area_ratio | 0.2 | 遮挡面积判定阈值，请求 `area_ratio` 未传时使用 |
| occlusion_detection.yolo_seg_weights_path | model/occlusion.pt | YOLO-seg 遮挡模型权重 |
| occlusion_detection.yolo_imgsz | 960 | YOLO-seg 推理尺寸 |
| aggregate_detection.default_modules | tilt/screen/quality_abnormal/occlusion | `/detect_all` 默认执行模块 |
| aggregate_detection.tilt_threshold | 1.5 | `/detect_all` 默认倾斜阈值 |
| aggregate_detection.screen_conf | 0.25 | `/detect_all` 默认屏幕置信度 |
| aggregate_detection.screen_iou | 0.45 | `/detect_all` 默认屏幕 NMS IoU |
| aggregate_detection.occlusion_threshold | 0.25 | `/detect_all` 默认遮挡置信度 |
| aggregate_detection.occlusion_area_ratio | 0.2 | `/detect_all` 默认遮挡面积判定阈值 |
| runtime.max_image_bytes | 10485760 | 单图最大 10MB |

### YOLO-seg 遮挡后端数据建议

遮挡检测默认使用 YOLO segmentation。后续扩充数据时建议：

- 单类标注：`occlusion`。
- 标注格式：YOLO segmentation 多边形格式；也可由 COCO/Labelme/CVAT 转换。
- 可行性实验：80–150 张遮挡正样本 + 200–500 张正常负样本。
- 第一版可用：300–500 张遮挡正样本 + 500–1000 张正常负样本。
- 生产稳定：1000+ 张遮挡正样本 + 2000+ 张正常负样本。

### Docker CUDA设备说明

| docker run | config yolo.device | 容器内实际设备 |
|------------|------------------|----------------|
| `--gpus all` | `"cuda:0"` / `"cuda:1"` | 对应容器可见CUDA编号 |
| `--gpus '"device=1"'` | `"cuda:0"` | 容器只看到选中的物理卡 |

---

## 接口明细表（填表格式）

> 以下表格列与 Excel 表头一致，可按行复制到接口登记表。

### 表头说明

| 列 | 含义 |
|----|------|
| 版本说明 | 接口版本 |
| 功能类型 | 业务分类 |
| URL地址 说明 | 路径说明 |
| 请求动作 | HTTP 方法 |
| 参数说明【请求】 | 入参名称 |
| 要求与否(R/O) | R=必填，O=可选 |
| 类型类型 | 数据类型 |
| 类型说明 | 参数说明 |
| 参数说明【应答】 | 出参名称 |
| 要求与否 | R/O |
| 类型类型 | 数据类型 |
| 类型说明 | 参数说明 |
| 错误码 | 业务/HTTP 错误码 |
| 错误码说明 | 说明 |
| 样例说明 | 请求/响应示例 |

---

### 3.1 倾斜检测 — 明细行

| 版本说明 | 功能类型 | URL地址 说明 | 请求动作 | 参数说明【请求】 | R/O | 类型 | 类型说明 | 参数说明【应答】 | R/O | 类型 | 类型说明 | 错误码 | 错误码说明 | 样例说明 |
|---------|---------|-------------|---------|----------------|-----|------|---------|----------------|-----|------|---------|------|---------|---------|
| v1.0.0 | 倾斜检测 | POST /detect_tilt | POST | Content-Type | R | string | application/json 或 text/plain | code | R | int | 200=成功 | 400 | 参数错误 | 见 §3 |
| v1.0.0 | 倾斜检测 | 同上 | POST | images | R | string | Base64（JSON） | msg | R | string | 描述 | 500 | 内部错误 | |
| v1.0.0 | 倾斜检测 | 同上 | POST | image | O | string | 同 images | start_time | R | string | 开始时间戳 | | | |
| v1.0.0 | 倾斜检测 | 同上 | POST | tilt_threshold | O | float | 角度阈值(度) | end_time | R | string | 结束时间戳 | | | |
| v1.0.0 | 倾斜检测 | 同上 | POST | Body(整体) | O | string | text/plain 时为纯 Base64 | tilt_threshold | R | float | 实际阈值 | | | |
| v1.0.0 | 倾斜检测 | 同上 | POST | | | | | result.is_tilted | R | boolean | 是否倾斜 | | | |
| v1.0.0 | 倾斜检测 | 同上 | POST | | | | | result.angle | R | float | 角度(度) | | | |
| v1.0.0 | 倾斜检测 | 同上 | POST | | | | | result.cost_ms | R | float | 耗时(ms) | | | |

---

### 4.1 屏幕检测 — 明细行

| 版本说明 | 功能类型 | URL地址 说明 | 请求动作 | 参数说明【请求】 | R/O | 类型 | 类型说明 | 参数说明【应答】 | R/O | 类型 | 类型说明 | 错误码 | 错误码说明 | 样例说明 |
|---------|---------|-------------|---------|----------------|-----|------|---------|----------------|-----|------|---------|------|---------|---------|
| v1.0.0 | 屏幕检测 | POST /detect_screen | POST | Content-Type | R | string | application/json | code | R | int | 200 | 400 | 参数错误 | 见 §4 |
| v1.0.0 | 屏幕检测 | 同上 | POST | images | R | string/array | Base64 单图或数组 | msg | R | string | 结果描述 | 500 | 内部错误 | |
| v1.0.0 | 屏幕检测 | 同上 | POST | conf | O | float | 置信度 | conf | R | float | 实际 conf | | | |
| v1.0.0 | 屏幕检测 | 同上 | POST | iou | O | float | NMS IoU | iou | R | float | 实际 iou | | | |
| v1.0.0 | 屏幕检测 | 同上 | POST | | | | | total | R | int | 图片数 | | | |
| v1.0.0 | 屏幕检测 | 同上 | POST | | | | | results[].primary.label | R | int | 0~3 | | | |
| v1.0.0 | 屏幕检测 | 同上 | POST | | | | | results[].primary.confidence | R | float | 置信度 | | | |
| v1.0.0 | 屏幕检测 | 同上 | POST | | | | | results[].primary.box | R | float[4] | x1,y1,x2,y2 | | | |
| v1.0.0 | 屏幕检测 | 同上 | POST | | | | | results[].detections | R | array | 全部有效框 | | | |

---

### 6.1 画面异常检测 — 明细行

| 版本说明 | 功能类型 | URL地址 说明 | 请求动作 | 参数说明【请求】 | R/O | 类型 | 类型说明 | 参数说明【应答】 | R/O | 类型 | 类型说明 | 错误码 | 错误码说明 | 样例说明 |
|---------|---------|-------------|---------|----------------|-----|------|---------|----------------|-----|------|---------|------|---------|---------|
| v1.0.0 | 画面异常检测 | POST /detect_quality_abnormal | POST | Content-Type | R | string | application/json | code | R | int | 200 | 400 | 参数/Base64/图片错误 | 见 §6 |
| v1.0.0 | 画面异常检测 | 同上 | POST | image | R | string | 单张图片 Base64 | msg | R | string | 结果描述 | 500 | 内部错误 | |
| v1.0.0 | 画面异常检测 | 同上 | POST | | | | | is_abnormal | R | boolean | 是否存在异常 | | | |
| v1.0.0 | 画面异常检测 | 同上 | POST | | | | | abnormal_types | R | int[] | 命中枚举：1虚焦 2偏色 3雪花噪点 4花屏 | | | |
| v1.0.0 | 画面异常检测 | 同上 | POST | | | | | results[].type | R | int | 命中异常类型 | | | |
| v1.0.0 | 画面异常检测 | 同上 | POST | | | | | results[].score | R | float | 0~1 | | | |
| v1.0.0 | 画面异常检测 | 同上 | POST | | | | | results[].message | R | string | 中文提示 | | | |
| v1.0.0 | 画面异常检测 | 同上 | POST | | | | | message | R | string | 总体中文提示 | | | |

---

### 7.1 镜头遮挡检测 — 明细行

| 版本说明 | 功能类型 | URL地址 说明 | 请求动作 | 参数说明【请求】 | R/O | 类型 | 类型说明 | 参数说明【应答】 | R/O | 类型 | 类型说明 | 错误码 | 错误码说明 | 样例说明 |
|---------|---------|-------------|---------|----------------|-----|------|---------|----------------|-----|------|---------|------|---------|---------|
| v1.0.0 | 镜头遮挡检测 | POST /detect_occlusion | POST | Content-Type | R | string | application/json | code | R | int | 200 | 400 | 参数/Base64/图片错误 | 见 §7 |
| v1.0.0 | 镜头遮挡检测 | 同上 | POST | image | R | string | 单张图片 Base64 | msg | R | string | 结果描述 | 500 | 内部错误 | |
| v1.0.0 | 镜头遮挡检测 | 同上 | POST | threshold | O | float | YOLO 置信度阈值 0~1；不传读配置 | threshold | R | float | 本次实际使用置信度阈值 | | | |
| v1.0.0 | 镜头遮挡检测 | 同上 | POST | area_ratio | O | float | 遮挡面积判定阈值 0~1；不传读配置 | area_ratio | R | float | 本次实际使用面积判定阈值 | | | |
| v1.0.0 | 镜头遮挡检测 | 同上 | POST | | | | | is_occluded | R | boolean | 是否镜头近处遮挡 | | | |
| v1.0.0 | 镜头遮挡检测 | 同上 | POST | | | | | occlusion_area_ratio | R | float | 遮挡面积占比 0~1 | | | |
| v1.0.0 | 镜头遮挡检测 | 同上 | POST | | | | | score | R | float | 检测分数 0~1 | | | |
| v1.0.0 | 镜头遮挡检测 | 同上 | POST | | | | | message | R | string | 中文提示 | | | |

---

### 2.1 健康检查 — 明细行

| 版本说明 | 功能类型 | URL地址 说明 | 请求动作 | 参数说明【请求】 | R/O | 类型 | 类型说明 | 参数说明【应答】 | R/O | 类型 | 类型说明 | 错误码 | 错误码说明 | 样例说明 |
|---------|---------|-------------|---------|----------------|-----|------|---------|----------------|-----|------|---------|------|---------|---------|
| v1.0.0 | 健康检查 | GET /health | GET | 无 | - | - | - | status | R | string | success/not_ready | 503 | 未就绪 | 见 §2 |
| v1.0.0 | 健康检查 | 同上 | GET | | | | | ready | R | boolean | 可接流量 | | | |
| v1.0.0 | 健康检查 | 同上 | GET | | | | | screen_model.loaded | R | boolean | 模型已加载 | | | |
| v1.0.0 | 健康检查 | 同上 | GET | | | | | screen_model.warmed_up | R | boolean | GPU已warmup | | | |

---

*文档版本：v1.1.0 | 更新日期：2026-07-20 | 对应仓库：screen_det*

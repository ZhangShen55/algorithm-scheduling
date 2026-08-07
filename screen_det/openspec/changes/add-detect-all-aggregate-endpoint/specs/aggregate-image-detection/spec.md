## ADDED Requirements

### Requirement: 聚合检测接口必须一次返回单图全部检测结果

系统 SHALL 提供 `POST /detect_all` 与 `POST /api/v1/detect_all` 接口，客户端 MUST 通过 JSON 请求体提交单张 Base64 图片。系统 SHALL 对同一张图片执行倾斜检测、屏幕/幕布类型检测、画面异常检测和镜头遮挡检测，并在一次响应中按模块返回结果。

#### Scenario: 全量检测成功

- **WHEN** 客户端提交合法 JSON 请求体且包含可解码的 `image`
- **THEN** 系统 SHALL 返回 HTTP 200，并返回 `tilt`、`screen`、`quality_abnormal`、`occlusion` 四个结果块
- **AND** 顶层响应 SHALL 包含 `code`、`msg`、`start_time`、`end_time`、`cost_ms`、`executed_modules`、`failed_modules`、`effective_params` 和 `problem_types`

#### Scenario: 请求图片非法

- **WHEN** 客户端未提交 `image` 字段、提交空字符串、提交非法 Base64、提交无法解码为图片的数据、或提交超过大小限制的图片
- **THEN** 系统 SHALL 返回 HTTP 400，并返回 `code=400` 和错误信息

#### Scenario: 非 JSON 请求

- **WHEN** 客户端以非 `application/json` 请求体调用 `/detect_all`
- **THEN** 系统 SHALL 返回 HTTP 400，并提示请求体必须为 JSON

### Requirement: 聚合检测请求必须支持阈值覆盖

聚合检测请求 SHALL 支持本次调用级参数覆盖。除 `image` 外，其他字段未传时 SHALL 使用 `[aggregate_detection]` 配置默认值；传入合法值时 SHALL 只影响本次 `/detect_all` 调用，不修改全局配置。

请求字段 SHALL 包含：

| 字段 | 必填 | 类型 | 合法范围 | 默认来源 |
|------|------|------|----------|----------|
| `image` | 是 | string | 非空 | 无 |
| `tilt_threshold` | 否 | float | 大于等于 0 | `[aggregate_detection].tilt_threshold` |
| `screen_conf` | 否 | float | 0 到 1 | `[aggregate_detection].screen_conf` |
| `screen_iou` | 否 | float | 0 到 1 | `[aggregate_detection].screen_iou` |
| `occlusion_threshold` | 否 | float | 0 到 1 | `[aggregate_detection].occlusion_threshold` |
| `occlusion_area_ratio` | 否 | float | 0 到 1 | `[aggregate_detection].occlusion_area_ratio` |
| `include` | 否 | string[] | 合法模块枚举 | `[aggregate_detection].default_modules` |

合法模块枚举 SHALL 为 `tilt`、`screen`、`quality_abnormal`、`occlusion`。

#### Scenario: 使用配置默认阈值

- **WHEN** 客户端只提交 `image`，未提交任何阈值字段
- **THEN** 系统 SHALL 使用 `[aggregate_detection]` 配置默认值执行检测
- **AND** 响应 `effective_params` SHALL 返回本次实际使用的阈值、模块列表和 YOLO 设备

#### Scenario: 使用请求覆盖阈值

- **WHEN** 客户端提交 `tilt_threshold=0.5`、`screen_conf=0.4`、`screen_iou=0.5`、`occlusion_threshold=0.3`、`occlusion_area_ratio=0.15`
- **THEN** 系统 SHALL 使用这些请求值执行本次检测
- **AND** 响应 `effective_params` SHALL 返回这些实际使用值

#### Scenario: 阈值参数非法

- **WHEN** 客户端提交超出合法范围的 `screen_conf`、`screen_iou`、`occlusion_threshold`、`occlusion_area_ratio`，或提交小于 0 的 `tilt_threshold`
- **THEN** 系统 SHALL 返回 HTTP 400

#### Scenario: include 只执行部分模块

- **WHEN** 客户端提交 `include=["tilt","quality_abnormal"]`
- **THEN** 系统 SHALL 只执行倾斜检测和画面异常检测
- **AND** 响应 `executed_modules` SHALL 为 `["tilt","quality_abnormal"]`
- **AND** 响应中的 `screen` 和 `occlusion` SHALL 为 `null`

#### Scenario: include 包含非法模块

- **WHEN** 客户端提交 `include` 且包含非 `tilt`、`screen`、`quality_abnormal`、`occlusion` 的值
- **THEN** 系统 SHALL 返回 HTTP 400

### Requirement: 聚合检测响应必须按模块隔离结果

聚合检测响应 SHALL 保持各检测模块结果相互独立。每个已执行模块的结果块 SHALL 包含 `code`、`msg` 和 `cost_ms`。未执行模块结果 SHALL 为 `null`。

#### Scenario: 顶层 problem_types 汇总模块级业务问题

- **WHEN** 一个或多个已执行且成功的子模块检测到业务问题
- **THEN** 顶层 `problem_types` SHALL 返回命中的模块级业务问题枚举数组

#### Scenario: 顶层 problem_types 无业务问题

- **WHEN** 所有已执行且成功的子模块均未检测到业务问题
- **THEN** 顶层 `problem_types` SHALL 为空数组

#### Scenario: 子模块失败不写入 problem_types

- **WHEN** 某个子模块执行失败并返回 `code=500`
- **THEN** 该模块 SHALL 加入 `failed_modules`
- **AND** 系统不 SHALL 因该模块失败而向 `problem_types` 添加业务问题枚举
- **AND** 该模块失败 SHALL 通过对应模块结果块和 `failed_modules` 表达

#### Scenario: 倾斜结果块

- **WHEN** `tilt` 模块被执行且检测成功
- **THEN** `tilt` 结果块 SHALL 包含 `code=200`、`msg`、`cost_ms` 和 `result`
- **AND** `result` SHALL 包含 `is_tilted` 和 `angle`

#### Scenario: 屏幕结果块

- **WHEN** `screen` 模块被执行且检测成功
- **THEN** `screen` 结果块 SHALL 包含 `code=200`、`msg`、`cost_ms`、`primary` 和 `detections`
- **AND** `primary` SHALL 为最高优先检测框或 `null`
- **AND** `detections` SHALL 为检测框数组

#### Scenario: 画面异常结果块

- **WHEN** `quality_abnormal` 模块被执行且检测成功
- **THEN** `quality_abnormal` 结果块 SHALL 包含 `code=200`、`msg`、`cost_ms`、`is_abnormal`、`abnormal_types`、`results` 和 `message`
- **AND** `abnormal_types` 中出现的类型 SHALL 在 `results` 中有对应结果

#### Scenario: 遮挡结果块

- **WHEN** `occlusion` 模块被执行且检测成功
- **THEN** `occlusion` 结果块 SHALL 包含 `code=200`、`msg`、`cost_ms`、`is_occluded`、`occlusion_area_ratio`、`score`、`threshold`、`area_ratio` 和 `message`
- **AND** `threshold` 和 `area_ratio` SHALL 返回本次遮挡检测实际使用值

### Requirement: 聚合检测 problem_types 必须使用稳定模块级枚举

系统 SHALL 使用稳定字符串枚举表达顶层模块级业务问题类型，便于北向按模块快速判断。具体问题细节 SHALL 保留在对应模块结果块中。

第一版 `problem_types` 合法值 SHALL 包含：

| 枚举 | 说明 |
|------|------|
| `tilt` | 画面歪斜 |
| `screen` | 屏幕/幕布检测发现业务问题，具体 label 或是否未识别看 `screen` 结果块 |
| `quality_abnormal` | 画面异常，具体类型看 `quality_abnormal.abnormal_types` |
| `occlusion` | 镜头遮挡 |

#### Scenario: 倾斜问题进入 problem_types

- **WHEN** `tilt.code=200` 且 `tilt.result.is_tilted=true`
- **THEN** `problem_types` SHALL 包含 `"tilt"`

#### Scenario: 屏幕问题进入 problem_types

- **WHEN** `screen.code=200` 且 `screen.primary.label` 为 `0`、`1` 或 `2`
- **THEN** `problem_types` SHALL 包含 `"screen"`

#### Scenario: 正常屏不进入 problem_types

- **WHEN** `screen.code=200` 且 `screen.primary.label=3`
- **THEN** `problem_types` 不 SHALL 包含 `"screen"`

#### Scenario: 未识别到屏幕进入 problem_types

- **WHEN** `screen.code=200` 且 `screen.primary=null`
- **THEN** `problem_types` SHALL 包含 `"screen"`

#### Scenario: 画面异常进入 problem_types

- **WHEN** `quality_abnormal.code=200` 且 `quality_abnormal.is_abnormal=true`
- **THEN** `problem_types` SHALL 包含 `"quality_abnormal"`

#### Scenario: 遮挡进入 problem_types

- **WHEN** `occlusion.code=200` 且 `occlusion.is_occluded=true`
- **THEN** `problem_types` SHALL 包含 `"occlusion"`

### Requirement: 聚合检测必须隔离子模块失败

系统 SHALL 默认隔离子模块失败。除请求参数或图片解码错误外，单个子模块失败不 SHALL 阻止其他模块返回结果。

#### Scenario: 单个子模块失败

- **WHEN** `screen` 模块执行失败，但 `tilt`、`quality_abnormal` 和 `occlusion` 执行成功
- **THEN** 系统 SHALL 返回 HTTP 200
- **AND** `screen.code` SHALL 为 `500`
- **AND** `failed_modules` SHALL 包含 `"screen"`
- **AND** 其他成功模块 SHALL 返回 `code=200`

### Requirement: 聚合检测必须通过配置控制默认行为

系统 SHALL 新增 `[aggregate_detection]` 配置段，控制聚合接口启用状态、默认执行模块、聚合接口默认阈值、遮挡面积判定阈值和聚合接口中 YOLO 推理设备。分接口 SHALL 继续使用各自原有配置段；`/detect_all` SHALL 使用 `[aggregate_detection]` 作为默认参数来源。

配置项 SHALL 包含：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `enabled` | `true` | 是否启用 `/detect_all` |
| `default_modules` | `["tilt","screen","quality_abnormal","occlusion"]` | 请求未传 `include` 时执行的模块 |
| `tilt_threshold` | `1.5` | 聚合接口默认倾斜角度阈值，单位度 |
| `screen_conf` | `0.25` | 聚合接口默认屏幕 YOLO 置信度阈值 |
| `screen_iou` | `0.45` | 聚合接口默认屏幕 YOLO NMS IoU 阈值 |
| `occlusion_threshold` | `0.25` | 聚合接口默认遮挡 YOLO-seg 置信度阈值 |
| `occlusion_area_ratio` | `0.2` | 聚合接口默认遮挡面积占比判定阈值 |
| `device` | `"cpu"` | 聚合接口中 YOLO 推理设备，支持 `"cpu"`、`"cuda:0"`、`"cuda:1"` 等字符串 |

#### Scenario: 聚合接口被禁用

- **WHEN** `[aggregate_detection].enabled=false`
- **THEN** 客户端调用 `/detect_all` SHALL 得到服务不可用或配置禁用响应

#### Scenario: 默认执行模块来自配置

- **WHEN** 请求未传 `include`
- **THEN** 系统 SHALL 使用 `[aggregate_detection].default_modules` 作为本次执行模块

#### Scenario: 默认阈值来自聚合配置

- **WHEN** 请求未传 `tilt_threshold`、`screen_conf`、`screen_iou`、`occlusion_threshold` 或 `occlusion_area_ratio`
- **THEN** 系统 SHALL 使用 `[aggregate_detection]` 中对应配置值作为本次检测参数

#### Scenario: YOLO 设备来自聚合配置

- **WHEN** `[aggregate_detection].device="cuda:0"`
- **THEN** `/detect_all` 中的屏幕检测和遮挡检测 SHALL 使用该设备执行 YOLO 推理
- **AND** 响应 `effective_params.device` SHALL 返回 `"cuda:0"`

#### Scenario: 聚合配置出现在配置查询中

- **WHEN** 客户端调用 `/config` 或 `/api/v1/config`
- **THEN** 响应 SHALL 包含 `aggregate_detection` 配置对象

### Requirement: 聚合检测必须保留现有分接口兼容

新增 `/detect_all` 不 SHALL 改变现有分接口的请求、响应和 URL 行为。

#### Scenario: 现有分接口继续可用

- **WHEN** 客户端调用 `/detect_tilt`、`/detect_screen`、`/detect_quality_abnormal`、`/detect_occlusion` 或 `/detect_inspect`
- **THEN** 系统 SHALL 保持这些接口的既有请求和响应兼容

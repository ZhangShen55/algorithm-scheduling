## Why

当前北向系统如果要对同一张图片完成完整检测，需要分别调用倾斜检测、屏幕类型检测、画面异常检测和镜头遮挡检测接口。这样会导致同一张图片被多次传输、解析和解码，也会增加北向调用次数、链路耗时和失败处理复杂度。

本次变更新增单图聚合检测接口，让北向一次提交图片即可获得全部检测结果，同时保持现有分接口不变。

## What Changes

- 新增 `/detect_all` 与 `/api/v1/detect_all` 接口。
- 请求体接收单张图片 Base64；除 `image` 必填外，其余请求字段均有 `config.toml` 默认值，调用方可按需传入覆盖本次调用：
  - `tilt_threshold`
  - `screen_conf`
  - `screen_iou`
  - `occlusion_threshold`
  - `occlusion_area_ratio`
  - `include`
- 响应按检测能力分块返回：
  - `tilt`
  - `screen`
  - `quality_abnormal`
  - `occlusion`
- 响应顶层返回 `effective_params`，明确本次实际使用的阈值、模块列表和 YOLO 设备。
- 响应顶层在 `effective_params` 后返回 `problem_types`，使用模块级枚举直接汇总哪些检测模块发现业务问题。
- 聚合接口内部应尽量复用一次图片解码结果，避免四个检测模块各自重复解码。
- 顶层响应用于表达聚合请求整体是否完成；各子模块通过自己的 `code`、`msg` 和 `cost_ms` 表达局部成功或失败。
- 保持已有 `/detect_tilt`、`/detect_screen`、`/detect_quality_abnormal`、`/detect_occlusion`、`/detect_inspect` 接口兼容。
- 更新 API 文档、README、部署验证脚本和单元测试。

## Capabilities

### New Capabilities

- `aggregate-image-detection`: 定义单张图片全量聚合检测能力，包括请求/响应结构、阈值覆盖、配置默认值、子模块错误隔离和耗时统计。

### Modified Capabilities

- 无。现有分接口能力保持兼容，不改变既有接口契约。

## Impact

- API：新增 `POST /detect_all` 与 `POST /api/v1/detect_all`。
- Schema：新增聚合检测请求/响应模型，复用或映射现有倾斜、屏幕、画面异常、遮挡响应字段。
- Service：新增聚合编排服务，复用现有检测服务能力。
- 配置：新增 `[aggregate_detection]` 配置段，用于控制聚合接口启用状态、默认执行模块、聚合接口默认阈值、遮挡判定面积阈值，以及聚合接口中 YOLO 推理使用的 `device`。
- 文档：更新 `docs/API接口文档.md` 和 README，明确请求报文、响应报文和释放出的配置参数。
- 测试：新增聚合接口单元测试和 HTTP 路由测试，覆盖默认阈值、请求覆盖阈值、子模块失败隔离和缺失/非法图片输入。

## 1. 配置与 Schema

- [x] 1.1 在 `app/core/config.py` 新增 `AggregateDetectionConfig`，包含 `enabled`、`default_modules`、`tilt_threshold`、`screen_conf`、`screen_iou`、`occlusion_threshold`、`occlusion_area_ratio`、`device`。
- [x] 1.2 在 `config.toml` 新增 `[aggregate_detection]` 默认配置。
- [x] 1.3 更新 `/config` 与 `/config/reload` 响应，返回 `aggregate_detection` 配置。
- [x] 1.4 新增 `app/schemas/aggregate.py`，定义 `/detect_all` 请求模型。
- [x] 1.5 新增 `app/schemas/aggregate.py`，定义 `tilt`、`screen`、`quality_abnormal`、`occlusion` 四个结果块模型。
- [x] 1.6 新增聚合响应模型，包含 `code`、`msg`、`start_time`、`end_time`、`cost_ms`、`executed_modules`、`failed_modules`、`effective_params`、`problem_types` 和四个模块结果块。
- [x] 1.7 为 `include` 模块枚举增加校验，合法值限定为 `tilt`、`screen`、`quality_abnormal`、`occlusion`。
- [x] 1.8 为 `screen_conf`、`screen_iou`、`occlusion_threshold`、`occlusion_area_ratio` 增加 `0~1` 校验，为 `tilt_threshold` 增加非负校验。
- [x] 1.9 为 `device` 增加配置读取与格式校验，支持 `"cpu"`、`"cuda:0"`、`"cuda:1"` 等字符串；`device` 不作为请求字段开放。
- [x] 1.10 定义顶层 `problem_types` 响应字段，枚举限定为 `tilt`、`screen`、`quality_abnormal`、`occlusion`。

## 2. 服务层编排

- [x] 2.1 新增 `app/services/aggregate_detector.py`，实现 `/detect_all` 的编排入口。
- [x] 2.2 实现聚合接口的单次 Base64 图片解码与输入校验。
- [x] 2.3 复用倾斜检测逻辑，生成 `tilt` 结果块和 `cost_ms`。
- [x] 2.4 复用屏幕检测逻辑，生成 `screen` 结果块和 `cost_ms`。
- [x] 2.5 复用画面异常检测逻辑，生成 `quality_abnormal` 结果块和 `cost_ms`。
- [x] 2.6 复用 YOLO-seg 遮挡检测逻辑，生成 `occlusion` 结果块和 `cost_ms`。
- [x] 2.7 实现请求参数覆盖与 `[aggregate_detection]` 默认值解析，返回 `effective_params` 实际使用值。
- [x] 2.8 实现 `include` 部分模块执行，未执行模块返回 `null`。
- [x] 2.9 实现子模块异常捕获，默认不影响其他模块，失败模块进入 `failed_modules`。
- [x] 2.10 第一版固定串行执行，不实现聚合层并发和子模块超时配置。
- [x] 2.11 聚合接口中的屏幕检测和遮挡检测使用 `[aggregate_detection].device` 指定的 YOLO 推理设备。
- [x] 2.12 根据已成功执行的子模块结果生成顶层 `problem_types`，汇总 `tilt`、`screen`、`quality_abnormal`、`occlusion`。
- [x] 2.13 在聚合响应中计算顶层 `cost_ms` 和中文 `msg`。

## 3. API 路由

- [x] 3.1 新增 `app/api/v1/aggregate.py` 路由文件。
- [x] 3.2 注册 `POST /detect_all` 到 v1 router，确保同时支持 `/detect_all` 与 `/api/v1/detect_all`。
- [x] 3.3 保持 `Content-Type=application/json` 要求，非 JSON 返回 HTTP 400。
- [x] 3.4 图片参数错误、Base64 错误、图片解码错误继续返回 HTTP 400。
- [x] 3.5 聚合接口禁用时返回明确错误。
- [x] 3.6 成功请求后更新 `app_state` 请求计数。

## 4. 测试

- [x] 4.1 增加聚合接口请求模型测试，覆盖默认值、阈值覆盖和非法阈值。
- [x] 4.2 增加 `include` 测试，覆盖全量模块、部分模块和非法模块。
- [x] 4.3 增加聚合服务层测试，使用 mock 子模块验证四个结果块结构。
- [x] 4.4 增加子模块失败隔离测试，验证单模块失败时其他模块仍返回。
- [x] 4.5 增加 `[aggregate_detection]` 默认阈值、`device` 配置和响应 `effective_params` 回显测试。
- [x] 4.6 增加 `problem_types` 生成测试，覆盖无问题、倾斜、屏幕异常或未识别、画面异常、遮挡和子模块失败不进入业务问题数组。
- [x] 4.7 增加 `/detect_all` HTTP 路由测试，验证成功响应结构。
- [x] 4.8 增加 `/api/v1/detect_all` HTTP 路由测试，验证前缀路由可用。
- [x] 4.9 增加非法 JSON、缺失 `image`、非法 Base64 返回 HTTP 400 的测试。
- [x] 4.10 增加 `/config` 输出包含 `aggregate_detection` 的测试。
- [x] 4.11 运行现有倾斜、屏幕、画面异常、遮挡测试，确保分接口兼容。

## 5. 文档与验收

- [x] 5.1 更新 `docs/API接口文档.md`，新增 `/detect_all` 请求报文说明。
- [x] 5.2 更新 `docs/API接口文档.md`，新增 `/detect_all` 响应报文说明。
- [x] 5.3 更新 `docs/API接口文档.md`，新增 `[aggregate_detection]` 配置说明。
- [x] 5.4 更新 README，说明聚合接口用途、字段和调用示例。
- [x] 5.5 更新 `scripts/deploy_verify_http.py`，增加 `/detect_all` 基本验收。
- [x] 5.6 本地启动服务，调用 `/detect_all` 验证默认阈值响应。
- [x] 5.7 本地启动服务，调用 `/detect_all` 验证请求阈值覆盖响应。
- [x] 5.8 本地调用 `/detect_all` 验证 `include` 部分模块响应。
- [x] 5.9 运行 `python -m unittest discover`。
- [x] 5.10 运行 `openspec validate add-detect-all-aggregate-endpoint`。

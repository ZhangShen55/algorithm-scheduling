## ADDED Requirements

### Requirement: 遮挡检测接口必须支持请求级阈值覆盖
系统 SHALL 保持 `/detect_occlusion` 与 `/api/v1/detect_occlusion` 的 URL 不变，客户端 MUST 通过 JSON 请求体提交单张 Base64 图片，并 MAY 通过可选字段 `threshold` 和 `area_ratio` 覆盖本次请求的默认阈值。

`threshold` SHALL 表示 YOLO 预测置信度阈值，用于过滤低置信度 mask；`area_ratio` SHALL 表示遮挡面积判定阈值，用于决定 `is_occluded`。

#### Scenario: YOLO 后端检测到镜头遮挡
- **WHEN** 客户端向 `/detect_occlusion` 或 `/api/v1/detect_occlusion` 提交包含单张 Base64 图片的 JSON 请求，且 YOLO 分割后端使用本次有效阈值判定存在镜头遮挡
- **THEN** 系统 SHALL 返回 `code=200`、`is_occluded=true`、大于 `0` 的 `occlusion_area_ratio`、范围为 `0` 到 `1` 的 `score`、本次实际使用的 `threshold` 和 `area_ratio`、以及中文 `message`

#### Scenario: YOLO 后端未检测到镜头遮挡
- **WHEN** 客户端提交正常无遮挡图片，且 YOLO 分割后端没有满足遮挡阈值的 mask
- **THEN** 系统 SHALL 返回 `code=200`、`is_occluded=false`、`occlusion_area_ratio=0`、范围为 `0` 到 `1` 的 `score`、本次实际使用的 `threshold` 和 `area_ratio`、以及 `message="未检测到镜头遮挡"`

#### Scenario: 请求未传阈值
- **WHEN** 客户端只提交 `image`，未提交 `threshold` 和 `area_ratio`
- **THEN** 系统 SHALL 使用 `[occlusion_detection]` 中配置的 `threshold` 和 `area_ratio` 默认值执行本次检测

#### Scenario: 请求覆盖阈值
- **WHEN** 客户端提交 `image`，同时提交合法的 `threshold` 和 `area_ratio`
- **THEN** 系统 SHALL 使用请求中的 `threshold` 和 `area_ratio` 执行本次检测，并在响应中返回这两个实际使用值

#### Scenario: 请求参数错误
- **WHEN** 客户端未提交 `image` 字段、提交非 JSON 请求体、提交非法 Base64、提交无法解码为图片的数据、或提交超出合法范围的 `threshold` / `area_ratio`
- **THEN** 系统 SHALL 返回 HTTP 400，并在响应体中返回 `code=400` 和错误信息

### Requirement: 遮挡检测响应必须返回本次实际阈值
系统 SHALL 在遮挡检测成功响应中返回本次实际使用的 `threshold` 和 `area_ratio`，使客户端能够确认判定条件来自配置默认值还是请求覆盖值。

#### Scenario: 使用默认阈值的响应
- **WHEN** 客户端未传 `threshold` 和 `area_ratio`，且配置默认值为 `threshold=0.25`、`area_ratio=0.2`
- **THEN** 系统 SHALL 在响应中返回 `"threshold": 0.25` 和 `"area_ratio": 0.2`

#### Scenario: 使用请求覆盖阈值的响应
- **WHEN** 客户端传入 `"threshold": 0.5` 和 `"area_ratio": 0.15`
- **THEN** 系统 SHALL 在响应中返回 `"threshold": 0.5` 和 `"area_ratio": 0.15`

### Requirement: 遮挡检测后端必须支持 YOLO 分割权重 best.pt
系统 SHALL 支持通过配置启用 YOLO segmentation 遮挡检测后端，并默认使用当前项目下的 `model/best.pt` 作为遮挡分割权重。

#### Scenario: 默认使用 YOLO 分割后端
- **WHEN** `[occlusion_detection].yolo_seg_weights_path` 指向可读的 `model/best.pt`
- **THEN** 系统 SHALL 使用该 YOLO 分割模型执行 `/detect_occlusion` 遮挡检测

#### Scenario: 权重文件不存在
- **WHEN** `[occlusion_detection].yolo_seg_weights_path` 指向的权重文件不存在或不可读
- **THEN** 系统 SHALL 返回明确错误，错误信息 MUST 包含权重路径，便于定位部署问题

### Requirement: YOLO 分割结果必须用于计算遮挡面积占比
系统 SHALL 使用 YOLO segmentation 输出的遮挡 mask 计算 `occlusion_area_ratio`，该字段表示遮挡 mask 并集面积占整张图像面积的比例，取值范围 SHALL 为 `0` 到 `1`。

#### Scenario: 单个遮挡 mask
- **WHEN** YOLO 分割后端输出一个满足置信度阈值的遮挡 mask
- **THEN** 系统 SHALL 用该 mask 面积除以整图面积得到 `occlusion_area_ratio`

#### Scenario: 多个遮挡 mask
- **WHEN** YOLO 分割后端输出多个满足置信度阈值的遮挡 mask
- **THEN** 系统 SHALL 先合并 mask 并集，再用并集面积除以整图面积得到 `occlusion_area_ratio`

#### Scenario: 没有有效 mask
- **WHEN** YOLO 分割后端没有输出 mask，或所有 mask 均低于置信度阈值
- **THEN** 系统 SHALL 返回 `is_occluded=false` 且 `occlusion_area_ratio=0`

### Requirement: 遮挡判定必须结合面积阈值和置信度阈值
系统 SHALL 使用配置化阈值判定是否遮挡。第一版默认 SHALL 使用 `threshold=0.25` 过滤 YOLO 结果，并使用 `area_ratio=0.2` 判定遮挡。

#### Scenario: 满足面积与置信度阈值
- **WHEN** YOLO 分割后端输出的有效 mask 并集面积占比大于等于本次实际使用的 `area_ratio`
- **THEN** 系统 SHALL 返回 `is_occluded=true`

#### Scenario: 面积低于阈值
- **WHEN** YOLO 分割后端输出有效 mask，但 mask 并集面积占比小于本次实际使用的 `area_ratio`
- **THEN** 系统 SHALL 返回 `is_occluded=false`，并将响应中的 `occlusion_area_ratio` 归零，避免将低面积误检暴露为遮挡结果

#### Scenario: 正常图误报控制
- **WHEN** 使用 `model/best.pt`、`imgsz=960`、`threshold=0.25` 对 1000 张已确认正常无遮挡图片执行批量验证
- **THEN** 系统 SHALL 在 `occlusion_area_ratio > 0.2` 的判定口径下得到 0 张遮挡误报

### Requirement: YOLO 遮挡后端必须可配置且不支持 OpenCV 回退
系统 SHALL 通过 `[occlusion_detection]` 配置 YOLO 遮挡后端的权重路径、推理尺寸、置信度阈值、面积阈值和推理设备。系统 SHALL NOT 提供 OpenCV 遮挡后端或后端切换配置。`config.toml` 中的 `threshold` 和 `area_ratio` SHALL 作为请求未传阈值时的默认值。

#### Scenario: 使用默认配置
- **WHEN** 服务使用默认 `[occlusion_detection]` 配置启动
- **THEN** 系统 SHALL 默认启用 `yolo_seg` 后端，使用 `model/best.pt`、`imgsz=960`、`threshold=0.25` 和 `area_ratio=0.2`

#### Scenario: 指定推理设备
- **WHEN** `[occlusion_detection].yolo_device` 被配置为 `cpu`、`mps`、`0`、`1` 或其他 Ultralytics 支持的设备标识
- **THEN** 系统 SHALL 将该设备标识传递给 YOLO 推理流程

### Requirement: YOLO 模型必须避免每次请求重复加载
系统 SHALL 缓存 YOLO 遮挡模型实例，避免每次 `/detect_occlusion` 请求都重新加载 `best.pt`。

#### Scenario: 首次请求加载模型
- **WHEN** 服务首次收到 YOLO 遮挡检测请求且模型尚未加载
- **THEN** 系统 SHALL 加载 `model/best.pt` 并缓存模型实例用于后续请求

#### Scenario: 后续请求复用模型
- **WHEN** 服务已经加载 YOLO 遮挡模型并再次收到遮挡检测请求
- **THEN** 系统 SHALL 复用已加载的模型实例执行推理

#### Scenario: 配置或权重路径变更
- **WHEN** 测试或运行时调用配置重载逻辑导致权重路径或 YOLO 推理配置变化
- **THEN** 系统 SHALL 能够清理或重建遮挡模型缓存，避免继续使用过期模型

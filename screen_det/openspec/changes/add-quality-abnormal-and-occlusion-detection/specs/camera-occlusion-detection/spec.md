## ADDED Requirements

### Requirement: 镜头遮挡检测接口
系统 SHALL 提供镜头遮挡检测接口，接收单张图片 Base64，并返回是否存在镜头近处遮挡、遮挡面积占比、检测分数和提示信息。

#### Scenario: 检测到镜头遮挡
- **WHEN** 客户端向 `/detect_occlusion` 或 `/api/v1/detect_occlusion` 提交包含单张 Base64 图片的请求，且图片存在镜头前或镜头不远处遮挡
- **THEN** 系统 SHALL 返回 `code=200`、`is_occluded=true`、大于 `0` 的 `occlusion_area_ratio`、范围为 `0` 到 `1` 的 `score` 和中文 `message`

#### Scenario: 未检测到镜头遮挡
- **WHEN** 客户端提交的图片未检测到镜头近处遮挡
- **THEN** 系统 SHALL 返回 `code=200`、`is_occluded=false`、`occlusion_area_ratio=0` 或接近 `0` 的数值、范围为 `0` 到 `1` 的 `score` 和 `message="未检测到镜头遮挡"`

### Requirement: 遮挡定义范围
系统 SHALL 将遮挡定义限定为镜头前或镜头不远处遮挡，不应把教室内部普通人物、桌椅、黑板、投影屏或教学设备本身判定为遮挡。

#### Scenario: 教室内部普通物体不算遮挡
- **WHEN** 图片中存在正常教室人物、桌椅、黑板、投影屏或讲台设备，但不存在镜头近处遮挡
- **THEN** 系统 SHALL 返回 `is_occluded=false`

#### Scenario: 镜头近处遮挡算遮挡
- **WHEN** 图片中存在靠近镜头的大面积遮挡物、线状遮挡物或近景模糊遮挡物
- **THEN** 系统 SHALL 返回 `is_occluded=true`

### Requirement: 遮挡面积占比
系统 SHALL 返回遮挡区域占整张图像面积的比例，字段名为 `occlusion_area_ratio`，取值范围 SHALL 为 `0` 到 `1`。

#### Scenario: 返回面积比例
- **WHEN** 系统检测到遮挡区域
- **THEN** 系统 SHALL 计算遮挡区域面积占整图面积比例，并返回 `occlusion_area_ratio`

#### Scenario: 无遮挡面积比例
- **WHEN** 系统未检测到遮挡
- **THEN** 系统 SHALL 返回 `occlusion_area_ratio=0` 或接近 `0` 的数值

### Requirement: 遮挡接口不输出遮挡物枚举
系统 SHALL 在第一版遮挡接口中不输出遮挡物类型枚举，只输出是否遮挡、面积占比、检测分数和提示信息。

#### Scenario: 响应不包含遮挡物类型
- **WHEN** 系统返回遮挡检测响应
- **THEN** 响应体 SHALL 不要求包含 `occlusion_type` 或遮挡物枚举字段

### Requirement: 遮挡检测后端
系统 SHALL 支持 OpenCV 规则作为初版遮挡检测后端，并预留后续使用单类 YOLO-seg 分割模型替换后端的能力。

#### Scenario: OpenCV 后端输出遮挡结果
- **WHEN** 遮挡检测后端配置为 OpenCV
- **THEN** 系统 SHALL 基于大面积近景遮挡、线状遮挡和近景模糊遮挡等规则生成遮挡候选区域，并输出 `is_occluded` 和 `occlusion_area_ratio`

#### Scenario: YOLO-seg 后端保持接口兼容
- **WHEN** 后续遮挡检测后端配置为 YOLO-seg 且模型权重可用
- **THEN** 系统 SHALL 基于分割 mask 计算 `occlusion_area_ratio`，并保持遮挡检测接口响应结构不变

### Requirement: 镜头遮挡错误处理
系统 SHALL 对无效请求返回明确错误，不得返回伪成功结果。

#### Scenario: Base64 无效
- **WHEN** 客户端提交无效 Base64 图片
- **THEN** 系统 SHALL 返回 HTTP 400 和包含 `code=400`、中文错误信息的响应体

#### Scenario: 图片超过大小限制
- **WHEN** 客户端提交的图片超过运行时最大图片大小限制
- **THEN** 系统 SHALL 返回 HTTP 400 和包含 `code=400`、中文错误信息的响应体

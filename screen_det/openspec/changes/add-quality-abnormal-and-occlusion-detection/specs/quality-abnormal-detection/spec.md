## ADDED Requirements

### Requirement: 画面异常检测接口
系统 SHALL 提供画面异常检测接口，接收单张图片 Base64，并返回是否存在画面异常、异常类型数组、异常明细数组和总体提示信息。

#### Scenario: 检测到单一画面异常
- **WHEN** 客户端向 `/detect_quality_abnormal` 或 `/api/v1/detect_quality_abnormal` 提交包含单张 Base64 图片的请求，且图片仅命中一种画面异常
- **THEN** 系统 SHALL 返回 `code=200`、`is_abnormal=true`、包含该异常枚举的 `abnormal_types`、包含一条对应异常明细的 `results` 和中文 `message`

#### Scenario: 未检测到画面异常
- **WHEN** 客户端提交的图片未命中任何画面异常
- **THEN** 系统 SHALL 返回 `code=200`、`is_abnormal=false`、`abnormal_types=[]`、`results=[]` 和 `message="未检测到画面异常"`

### Requirement: 画面异常类型枚举
系统 SHALL 使用固定整数枚举表示画面异常类型：`1=虚焦`、`2=偏色`、`3=雪花噪点`、`4=花屏`。

#### Scenario: 返回异常类型枚举
- **WHEN** 系统检测到虚焦、偏色、雪花噪点或花屏
- **THEN** 系统 SHALL 只在 `abnormal_types` 和 `results[].type` 中返回 `1`、`2`、`3`、`4` 中对应的枚举值

### Requirement: 多异常同时返回
系统 SHALL 支持同一张图片同时命中多种画面异常，并保证 `abnormal_types` 与 `results` 中的类型一致。

#### Scenario: 检测到多种画面异常
- **WHEN** 图片同时命中虚焦和花屏
- **THEN** 系统 SHALL 返回 `is_abnormal=true`、`abnormal_types=[1,4]` 或等价顺序数组，并在 `results` 中仅包含 `type=1` 和 `type=4` 的异常明细

#### Scenario: results 只包含命中项
- **WHEN** 图片只命中偏色
- **THEN** 系统 SHALL 返回 `abnormal_types=[2]`，且 `results` 中不得包含虚焦、雪花噪点或花屏的未命中明细

### Requirement: 画面异常明细
系统 SHALL 为每个命中的画面异常返回 `type`、`score` 和 `message`。

#### Scenario: 异常明细字段完整
- **WHEN** 系统返回任一画面异常明细
- **THEN** 每个 `results` 元素 SHALL 包含整数 `type`、范围为 `0` 到 `1` 的 `score` 和中文 `message`

### Requirement: 画面异常检测算法
系统 SHALL 使用 OpenCV 规则实现画面异常初版检测，并按偏色、雪花噪点、虚焦、花屏的顺序计算和聚合结果。

#### Scenario: 偏色检测
- **WHEN** 图片存在明显全局色偏
- **THEN** 系统 SHALL 能够基于 Lab/RGB/HSV 色彩指标输出 `type=2` 的异常明细

#### Scenario: 雪花噪点检测
- **WHEN** 图片存在明显雪花噪点
- **THEN** 系统 SHALL 能够基于高频噪声、孤立噪点或边缘密度指标输出 `type=3` 的异常明细

#### Scenario: 虚焦检测
- **WHEN** 图片存在明显虚焦
- **THEN** 系统 SHALL 能够基于清晰度、梯度或边缘密度指标输出 `type=1` 的异常明细

#### Scenario: 花屏检测
- **WHEN** 图片存在明显块状花屏、马赛克污染或局部解码异常
- **THEN** 系统 SHALL 能够基于分块异常区域比例输出 `type=4` 的异常明细

### Requirement: 画面异常错误处理
系统 SHALL 对无效请求返回明确错误，不得返回伪成功结果。

#### Scenario: Base64 无效
- **WHEN** 客户端提交无效 Base64 图片
- **THEN** 系统 SHALL 返回 HTTP 400 和包含 `code=400`、中文错误信息的响应体

#### Scenario: 图片超过大小限制
- **WHEN** 客户端提交的图片超过运行时最大图片大小限制
- **THEN** 系统 SHALL 返回 HTTP 400 和包含 `code=400`、中文错误信息的响应体

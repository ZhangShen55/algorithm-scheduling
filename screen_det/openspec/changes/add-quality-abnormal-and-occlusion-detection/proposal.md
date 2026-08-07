## Why

当前服务已具备画面歪斜检测和屏幕类型检测，但业务样例中还存在虚焦、偏色、雪花噪点、花屏以及镜头近处遮挡等异常。现有接口和模型不能表达这些异常，也不能返回遮挡面积占比，导致图像质量问题无法被统一识别和上报。

本次变更目标是新增两个独立接口：画面异常检测接口和镜头遮挡检测接口。画面异常先采用 OpenCV 规则实现，花屏初版目标准确率约 70%；镜头遮挡先提供稳定接口和面积占比输出，使用 OpenCV 规则作为可运行初版，并在设计上预留 YOLO 分割模型替换能力。

## What Changes

- 新增画面异常检测接口，输入单张图片 Base64，输出是否异常、异常类型数组、命中的异常明细和提示信息。
- 画面异常类型枚举固定为：`1=虚焦`、`2=偏色`、`3=雪花噪点`、`4=花屏`。
- 画面异常接口允许同一张图片命中多种异常；`abnormal_types` 中出现的类型必须在 `results` 中有对应明细。
- 画面异常检测初版全部使用 OpenCV 规则：
  - 虚焦：清晰度/梯度/边缘密度指标，结合噪声修正。
  - 偏色：Lab/RGB/HSV 色彩偏移指标。
  - 雪花噪点：高频噪声、孤立噪点和边缘密度异常指标。
  - 花屏：固定切块、异常块打分、异常区域合并和面积比例判断，初版目标准确率约 70%。
- 新增镜头遮挡检测接口，输入单张图片 Base64，输出是否遮挡、遮挡面积占比、检测分数和提示信息。
- 遮挡定义限定为“镜头前或镜头不远处遮挡”，不包含教室内部普通物体遮挡。
- 遮挡接口初版不输出遮挡物枚举类型，仅输出 `is_occluded` 和 `occlusion_area_ratio`。
- 遮挡检测初版使用 OpenCV 规则估算遮挡区域；后续可通过单类 YOLO-seg 分割模型替换内部检测器，接口保持不变。
- 新增配置项、Pydantic schema、路由、服务模块、文档和验收脚本。
- 不引入破坏性变更；现有 `/detect_tilt`、`/detect_screen`、`/detect_inspect` 行为保持兼容。

## Capabilities

### New Capabilities

- `quality-abnormal-detection`: 定义画面异常检测能力，包括虚焦、偏色、雪花噪点、花屏的输入输出、枚举、评分和多异常返回规则。
- `camera-occlusion-detection`: 定义镜头近处遮挡检测能力，包括遮挡判定、遮挡面积占比、检测分数、提示信息以及后续 YOLO-seg 后端兼容要求。

### Modified Capabilities

- 无。

## Impact

- API：新增 `/detect_quality_abnormal` 与 `/api/v1/detect_quality_abnormal`。
- API：新增 `/detect_occlusion` 与 `/api/v1/detect_occlusion`。
- 代码：新增画面异常和遮挡相关 schema、route、service，并挂载到现有 `app/api/v1/router.py`。
- 配置：新增 `[quality_abnormal_detection]` 与 `[occlusion_detection]` 配置段，用于阈值、开关、分析尺寸和后端选择。
- 文档：更新 API 文档、README/Agent 维护说明和本地验收说明。
- 测试：新增基于 `test/图像检测` 的画面异常和遮挡验收用例。
- 部署：OpenCV 规则版不新增 GPU 依赖；若后续启用 YOLO-seg 遮挡后端，需要额外模型权重、数据标注和 GPU 推理资源。

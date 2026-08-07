## Why

当前 `/detect_occlusion` 使用 OpenCV 规则估算镜头遮挡区域，已经在真实样例上出现与人眼判断不一致的问题。现在项目下已有训练完成的单类 YOLO 分割权重 `model/best.pt`，并且在 1000 张正常无遮挡图上按 `occlusion_area_ratio > 0.2` 评估误报为 0 张，因此应将遮挡检测后端升级为 YOLO-seg，提高遮挡位置和面积占比的可靠性。

## What Changes

- 将 `/detect_occlusion` 和 `/api/v1/detect_occlusion` 的内部检测后端从 OpenCV 规则切换为 YOLO 分割模型。
- 默认权重路径使用当前项目下的 `model/best.pt`。
- 保持现有接口主体兼容，并扩展阈值覆盖能力：
  - 请求仍必须包含单张图片 Base64。
  - 请求可选传入 `threshold` 和 `area_ratio`，用于覆盖 `config.toml` 默认阈值。
  - 响应仍包含 `is_occluded`、`occlusion_area_ratio`、`score`、`message`。
  - 响应新增返回本次实际使用的 `threshold` 和 `area_ratio`，便于排查单次判定。
- 使用 YOLO 分割 mask 的并集面积计算 `occlusion_area_ratio`。
- 第一版建议默认 `threshold=0.25` 过滤 YOLO 低置信度结果，默认 `area_ratio=0.2` 判定遮挡。
- 移除 OpenCV 遮挡后端与相关配置，遮挡检测只使用 YOLO-seg。
- 增加 YOLO 遮挡后端的单元测试、接口测试和批量正常图误报验证脚本。

## Capabilities

### New Capabilities

- `camera-occlusion-detection`: 定义镜头遮挡检测接口在 YOLO-seg 后端下的行为，包括模型加载、mask 面积计算、阈值判定、响应兼容性、错误处理和验证要求。

### Modified Capabilities

- 无。当前仓库尚未将上一轮 `camera-occlusion-detection` 规格归档到 `openspec/specs/`，本次在新 change 下创建完整能力规格。

## Impact

- API：`/detect_occlusion` 与 `/api/v1/detect_occlusion` URL 保持不变；请求新增可选阈值字段；响应新增本次实际阈值字段。
- 配置：更新 `[occlusion_detection]`，移除 `backend` 配置，权重路径使用 `model/best.pt`，保留 `threshold` 与 `area_ratio` 默认值。
- 代码：
  - `app/services/occlusion_detector.py` 增加 YOLO-seg 推理后端。
  - 可能新增独立的 YOLO 遮挡模型加载器，避免每次请求重复加载权重。
  - `app/core/config.py` 增加 YOLO-seg 相关配置字段。
  - `app/api/v1/config.py`、文档和测试同步更新。
- 依赖：运行环境需要 `ultralytics`、`torch` 以及可用 CPU/GPU 推理环境。
- 模型文件：服务启动或首次请求需要能访问 `model/best.pt`。
- 性能：YOLO-seg 比 OpenCV 更重，建议生产环境使用 GPU；本机 Mac CPU 推理较慢，MPS 可能存在 PyTorch buffer 限制，需要支持配置设备选择。

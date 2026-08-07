## 1. 配置与依赖确认

- [x] 1.1 确认运行环境已安装 `ultralytics` 与 `torch`，并在缺失时给出清晰错误提示。
- [x] 1.2 更新 `app/core/config.py` 的 `OcclusionDetectionConfig`，新增或明确 `threshold`、`area_ratio`、`yolo_imgsz`、`yolo_device`、`yolo_retina_masks` 等 YOLO 推理配置。
- [x] 1.3 更新 `config.toml` 的 `[occlusion_detection]` 默认值：`yolo_seg_weights_path="model/best.pt"`、`area_ratio=0.2`、`threshold=0.25`、`yolo_imgsz=960`。
- [x] 1.4 确认 `model/best.pt` 路径解析基于项目根目录，避免不同启动目录导致权重找不到。

## 2. YOLO-seg 遮挡检测后端

- [x] 2.1 在 `app/services/occlusion_detector.py` 中实现 `yolo_seg` 后端分支，并移除现有 `opencv` 后端分支。
- [x] 2.2 实现 YOLO 模型加载函数，加载 `model/best.pt` 并校验权重文件存在。
- [x] 2.3 实现模型实例缓存，避免每次请求重复加载权重。
- [x] 2.4 实现模型缓存清理函数，供测试和配置重载场景使用。
- [x] 2.5 将 Base64 图片解码后的 BGR 图像转换为 YOLO 可接受的推理输入。
- [x] 2.6 调用 Ultralytics YOLO segmentation 推理，使用本次有效 `imgsz`、`threshold`、`device` 和 `retina_masks`。
- [x] 2.7 过滤低于本次有效 `threshold` 的预测结果，合并剩余 mask 并集。
- [x] 2.8 基于 mask 并集面积计算 `occlusion_area_ratio`。
- [x] 2.9 基于 `occlusion_area_ratio >= 本次有效 area_ratio` 判定 `is_occluded`。
- [x] 2.10 当面积低于阈值或无有效 mask 时，返回 `is_occluded=false` 且 `occlusion_area_ratio=0`。
- [x] 2.11 定义 YOLO 后端 `score` 计算方式，并保证返回值范围为 `0~1`。

## 3. 接口与错误处理

- [x] 3.1 更新 `app/schemas/occlusion.py` 请求模型，新增可选 `threshold` 和 `area_ratio` 字段，合法范围为 `0~1`。
- [x] 3.2 更新 `app/schemas/occlusion.py` 响应模型，新增 `threshold` 和 `area_ratio` 字段，表示本次实际使用阈值。
- [x] 3.3 保持 `/detect_occlusion` 与 `/api/v1/detect_occlusion` 的 URL 和 `image` 请求字段兼容。
- [x] 3.4 实现请求级阈值覆盖：请求未传时使用 `config.toml` 默认值，请求传入时只覆盖本次检测。
- [x] 3.5 对非法 Base64、无法解码图片、缺失 `image` 字段、非法 `threshold` 或非法 `area_ratio` 继续返回 HTTP 400。
- [x] 3.6 对权重缺失、YOLO 依赖缺失、推理失败等服务端问题返回可定位的错误信息。
- [x] 3.7 更新 `/config` 输出，确保新增 YOLO 遮挡配置字段可见。

## 4. 测试

- [x] 4.1 为 YOLO 后端增加单元测试，使用 mock YOLO 结果验证 mask 面积并集计算。
- [x] 4.2 增加测试覆盖：无 mask、低置信度 mask、面积低于阈值、面积超过阈值、多 mask 重叠。
- [x] 4.3 更新现有 `/detect_occlusion` 接口测试，确保响应结构兼容。
- [x] 4.4 增加请求未传阈值时使用配置默认值的测试。
- [x] 4.5 增加请求传入 `threshold` 和 `area_ratio` 覆盖默认值的测试。
- [x] 4.6 增加非法 `threshold` 和非法 `area_ratio` 返回 HTTP 400 的测试。
- [x] 4.7 增加权重路径不存在时的错误测试。
- [x] 4.8 确保现有画面异常检测、倾斜检测、屏幕检测相关测试不受影响。

## 5. 批量验证脚本

- [x] 5.1 新增或更新批量 YOLO 遮挡评估脚本，支持输入图片目录、权重路径、`imgsz`、`conf`、面积阈值和设备。
- [x] 5.2 脚本输出总图片数、任意 mask 数、不同面积阈值下的命中数、topN 疑似样本 CSV/JSON。
- [x] 5.3 使用 1000 张正常图验证 `occlusion_area_ratio > 0.2` 的误报数为 0。
- [x] 5.4 支持可选输出预测 mask 可视化图，便于人工复核真实遮挡样例。

## 6. 文档

- [x] 6.1 更新 `docs/API接口文档.md`，说明 `/detect_occlusion` 默认使用 YOLO-seg 后端。
- [x] 6.2 文档说明 `occlusion_area_ratio` 由 YOLO mask 并集面积计算。
- [x] 6.3 文档说明请求可选字段 `threshold` 和 `area_ratio`，以及二者覆盖 `config.toml` 默认值的规则。
- [x] 6.4 文档说明响应返回本次实际使用的 `threshold` 和 `area_ratio`。
- [x] 6.5 文档说明第一版默认阈值：`threshold=0.25`、`area_ratio=0.2`。
- [x] 6.6 文档记录正常图 1000 张验证结果：`area_ratio > 0.2` 误报 0 张。
- [x] 6.7 文档说明不再支持 OpenCV 遮挡后端或配置回退。
- [x] 6.8 更新 README 或部署说明，补充 `ultralytics/torch` 依赖、模型文件路径和 GPU/CPU/MPS 注意事项。

## 7. 最终验证

- [x] 7.1 运行遮挡检测相关单元测试。
- [x] 7.2 启动本地服务并调用 `/detect_occlusion`，确认 HTTP 响应结构兼容。
- [x] 7.3 调用 `/detect_occlusion` 时分别验证默认阈值和请求覆盖阈值，确认响应返回实际使用的 `threshold` 与 `area_ratio`。
- [x] 7.4 使用 `model/best.pt` 对 1000 张正常图跑批量验证，确认 `occlusion_area_ratio > 0.2` 命中 0 张。
- [x] 7.5 对真实遮挡样例输出可视化 mask，并人工确认预测区域贴近真实遮挡物。
- [x] 7.6 记录最终验证命令和结果。

## 8. 验证记录

- [x] `/Users/zhangshen/miniconda3/envs/screen_det/bin/python -m unittest tests.test_quality_occlusion`：24 tests OK。
- [x] `/Users/zhangshen/miniconda3/envs/screen_det/bin/python -m unittest discover`：24 tests OK。
- [x] `/Users/zhangshen/miniconda3/envs/screen_det/bin/python -m compileall app scripts tests`：通过。
- [x] `openspec validate use-yolo-seg-for-occlusion-detection`：valid。
- [x] 本地启动 `uvicorn app.main:app --host 127.0.0.1 --port 8891`，调用 `/detect_occlusion` 默认阈值返回 `threshold=0.25`、`area_ratio=0.2`，请求覆盖返回 `threshold=0.5`、`area_ratio=0.15`。
- [x] `/Users/zhangshen/miniconda3/envs/screen_det/bin/python scripts/evaluate_yolo_occlusion.py --images /Users/zhangshen/Documents/data/西交大老师画面图/images --output-dir test/reports/yolo_occlusion_normal_1000_apply --imgsz 960 --threshold 0.25 --area-ratio 0.2 --device cpu --batch 8 --top-n 20`：1000 张正常图，`any_prediction_count=29`，`occluded_count=0`，`counts_by_area_ratio_gt["0.2"]=0`。
- [x] `/Users/zhangshen/miniconda3/envs/screen_det/bin/python scripts/evaluate_yolo_occlusion.py --images test/图像检测/遮挡 --output-dir test/reports/yolo_occlusion_samples_overlay_apply --imgsz 960 --threshold 0.25 --area-ratio 0.2 --device cpu --batch 4 --top-n 10 --save-overlays`：25 张遮挡样例，生成 overlay，`occluded_count=7`。
- [x] `/Users/zhangshen/miniconda3/envs/screen_det/bin/python scripts/validate_occlusion_samples.py`：YOLO 已确认遮挡正例与正常负例通过，其他目录样例仅报告。

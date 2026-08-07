## Context

当前服务是 FastAPI + OpenCV + YOLO 的图像检测服务，已经提供 `/detect_occlusion` 与 `/api/v1/detect_occlusion` 两个镜头遮挡检测入口。现有遮挡检测后端是 OpenCV 规则，主要通过颜色、线段、模糊前景区域等启发式规则估算遮挡区域。该方案实现成本低，但在真实样例上已经出现遮挡区域与人眼判断不匹配的问题。

项目当前已有训练完成的单类 YOLO segmentation 权重 `model/best.pt`。该模型使用 `YOLO11s-seg` 训练，验证集上 `Mask mAP50≈0.847`、`Mask mAP50-95≈0.649`。在 1000 张已确认正常无遮挡图片上，使用 `imgsz=960`、`threshold=0.25` 推理时：

- 任意 mask 预测：29 张。
- `occlusion_area_ratio > 0.1`：6 张。
- `occlusion_area_ratio > 0.2`：0 张。

因此第一版生产判定应优先采用较保守的面积阈值 `0.2`，用更低误报率验证方案。后续如果真实遮挡图漏检较多，再根据正样本评估下调到 `0.1~0.15`。

## Goals / Non-Goals

**Goals:**

- 将 `/detect_occlusion` 的默认检测后端升级为 YOLO-seg。
- 默认使用项目下 `model/best.pt`。
- 保持现有接口 URL 和核心输入输出兼容，并支持请求级阈值覆盖。
- 使用 YOLO 分割 mask 并集面积计算 `occlusion_area_ratio`。
- 使用配置化阈值控制误报：默认 `threshold=0.25`、`area_ratio=0.2`。
- 在成功响应中返回本次实际使用的 `threshold` 和 `area_ratio`。
- 缓存 YOLO 模型实例，避免每次请求重复加载权重。
- 移除 OpenCV 遮挡后端和相关配置，避免线上存在两套面积语义。
- 增加验证脚本，支持对正常图集合统计误报数量。

**Non-Goals:**

- 本次不重新训练模型。
- 本次不新增遮挡物类别枚举。
- 本次不把 YOLO 遮挡模型合并到 `/detect_screen` 的屏幕类型检测模型中。
- 本次不承诺 Mac MPS 后端稳定可用；MPS 可作为配置项尝试，但生产建议使用 CUDA 或 CPU 回退。
- 本次不实现多帧时序判断，仅处理单张 Base64 图片。

## Decisions

### Decision 1: 默认后端切换为 `yolo_seg`

移除 `[occlusion_detection].backend` 配置，遮挡检测固定使用 YOLO-seg。OpenCV 规则在遮挡区域定位上已经不可靠，而 YOLO 分割可以直接输出遮挡 mask，更符合接口里 `occlusion_area_ratio` 的语义。

备选方案：

- 继续使用 OpenCV 并调阈值：成本低，但无法解决遮挡物形态复杂导致的泛化问题。
- YOLO 检测框而非分割：可以判断是否遮挡，但面积占比会被 bbox 放大，不适合当前接口。
- YOLO 分割：能给出像素级 mask，最适合当前“是否遮挡 + 遮挡面积占比”的接口。

结论：只使用 YOLO segmentation，不保留 OpenCV 遮挡回退。

### Decision 2: 模型路径默认使用 `model/best.pt`

当前 `best.pt` 已放到项目 `model/` 目录下，且是训练过程中验证指标最好的权重。配置默认值应改为：

```toml
[occlusion_detection]
yolo_seg_weights_path = "model/best.pt"
```

实现时应通过项目根目录解析相对路径，避免服务启动目录不同导致找不到权重。

### Decision 3: 面积使用 mask 并集计算

YOLO 可能输出多个遮挡 mask。接口只需要返回一个总体遮挡面积占比，因此应将所有满足置信度阈值的 mask 合并为并集，再计算：

```text
occlusion_area_ratio = union(mask_pixels) / image_pixels
```

这样可以避免多个 mask 重叠时重复计数，也比取最大单个 mask 更符合“整张图被遮挡多少”的业务含义。

### Decision 4: 第一版默认阈值使用 `threshold=0.25` 与 `area_ratio=0.2`

基于 1000 张正常图评估，`area_ratio > 0.2` 没有误报。第一版目标是先保证正常教室画面不误报，因此默认阈值应保守：

```toml
threshold = 0.25
area_ratio = 0.2
```

注意：之前 OpenCV 版本的 `occlusion_area_threshold=0.05` 不应直接迁移到 YOLO 后端。YOLO mask 的面积更接近真实遮挡区域，阈值含义发生变化；并且正常图评估显示 `0.05` 会产生 7 张误报。

后续调参建议：

- 如果真实遮挡图漏检明显，可评估 `0.15` 或 `0.1`。
- 阈值调整必须同时看正常图误报和遮挡图召回，不能只看一边。

字段命名约定：

- `threshold`：YOLO 置信度阈值，用于过滤低置信度 mask。
- `area_ratio`：遮挡面积占比判定阈值，用于决定 `is_occluded`。
- `occlusion_area_ratio`：模型预测出的遮挡 mask 面积占整图比例，是输出结果，不是阈值。

### Decision 4.1: 请求阈值覆盖配置默认值

`config.toml` 是默认值来源，请求入参可以不传阈值；如果传入合法阈值，则只覆盖本次请求，不修改全局配置。

请求示例：

```json
{
  "image": "base64字符串",
  "threshold": 0.25,
  "area_ratio": 0.2
}
```

有效阈值解析规则：

```text
effective_threshold = request.threshold ?? config.occlusion_detection.threshold
effective_area_ratio = request.area_ratio ?? config.occlusion_detection.area_ratio
```

两个字段都应限制在 `0~1` 范围内。非法值返回 HTTP 400。

### Decision 4.2: 响应返回本次实际阈值

由于请求可以覆盖配置默认值，成功响应必须返回本次实际使用的 `threshold` 和 `area_ratio`。这样线上排查时可以直接从响应判断本次检测条件。

遮挡响应示例：

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

无遮挡响应示例：

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

### Decision 5: 分数 `score` 使用模型置信度与面积比例综合表达

现有响应包含 `score`。YOLO 后端可将 `score` 定义为模型置信度和面积阈值通过程度的综合值，例如：

```text
score = max_mask_conf * clamp(occlusion_area_ratio / area_ratio, 0, 1)
```

也可以第一版直接使用满足阈值 mask 的最大 `conf`。推荐使用综合分数，因为仅使用 `conf` 不能表达遮挡面积是否接近阈值。

接口语义：

- `is_occluded` 是最终业务判定。
- `occlusion_area_ratio` 是遮挡面积占比。
- `score` 是当前判定的综合可信度，不等同于 YOLO 原始 conf。

### Decision 6: 模型加载采用懒加载 + 缓存

YOLO 权重加载成本较高，不应每个请求都重新加载。推荐实现一个模块级模型缓存：

- 首次 YOLO 遮挡请求时加载权重。
- 后续请求复用模型实例。
- 测试或配置重载时提供清理缓存函数。

当前服务已有屏幕检测 YOLO 的加载经验，可以复用类似模式，但遮挡检测不必一开始强制启动预加载。第一版可懒加载，后续如果部署需要 ready 检查，再增加启动预加载和 health 状态。

### Decision 7: 推理设备配置独立于屏幕检测

遮挡 YOLO 与屏幕检测 YOLO 是不同模型，应独立配置设备：

```toml
yolo_device = "cpu"
```

或生产环境：

```toml
yolo_device = "0"
```

原因：

- 本机 Mac MPS 对 `YOLO11s-seg + imgsz=960` 曾出现 buffer size 错误，不适合作为默认强依赖。
- CPU 可用但较慢。
- 生产环境 CUDA 更稳定。

实现上将 `yolo_device` 原样传给 Ultralytics `predict(device=...)`。

### Decision 8: 接口错误保持现有风格

请求错误继续返回 HTTP 400；推理异常继续返回 HTTP 500。权重缺失、依赖缺失、后端不支持等服务端配置问题应给出明确错误信息，便于部署排查。

如果后续需要服务启动时强校验模型，可单独增加配置项，例如 `preload_at_startup`。本次先不强制，避免本地开发因为没有 YOLO 运行环境而无法启动服务。

## Risks / Trade-offs

- [Risk] 验证集和训练数据仍偏少，当前指标不能代表所有真实场景。→ Mitigation: 第一版以保守阈值上线，并用正常图集和真实遮挡图集持续评估误报/漏检。
- [Risk] `area_ratio=0.2` 可能漏掉细线、小面积贴边遮挡。→ Mitigation: 后续基于正样本召回率评估是否下调到 `0.1~0.15`，或补充线状遮挡数据重新训练。
- [Risk] Mac MPS 推理可能不稳定。→ Mitigation: 配置支持 `cpu`、`mps`、CUDA 设备；默认文档建议生产 CUDA，本地可 CPU 小批量验证。
- [Risk] YOLO 依赖增加部署复杂度。→ Mitigation: 文档明确 `ultralytics/torch` 依赖；部署问题直接返回可定位错误，不自动切换到旧规则。
- [Risk] 多 worker 部署会为每个 worker 加载一份 YOLO 权重，占用更多显存。→ Mitigation: 文档建议 YOLO 后端部署时控制 worker 数，或使用单独推理服务。
- [Risk] 只返回 `is_occluded=false` 时将低面积 mask 归零，可能隐藏调试信息。→ Mitigation: 生产接口保持简洁；调试脚本和日志可记录原始 mask 面积与 conf。

## Migration Plan

1. 更新配置默认值：移除 `backend` 与 OpenCV 遮挡参数，保留 `yolo_seg_weights_path="model/best.pt"`、`area_ratio=0.2`、`threshold=0.25`、`yolo_imgsz=960`。
2. 实现 YOLO-seg 后端，并移除 OpenCV 遮挡后端。
3. 增加模型缓存和缓存重置逻辑。
4. 更新 `/config` 输出、API 文档和 README。
5. 运行单元测试和接口测试。
6. 使用 1000 张正常图批量验证误报：`occlusion_area_ratio > 0.2` 应为 0 张。
7. 使用真实遮挡样例进行人工可视化复核，确认 mask 是否贴合。

Rollback：

- 如需恢复旧 OpenCV 遮挡规则，应通过新的变更重新引入，不再通过配置切换。
- 如果模型文件不可用或 YOLO 依赖不可用，应修复部署环境或临时回滚代码版本。

## Open Questions

- 是否需要在接口响应中增加调试字段，例如原始 `max_conf` 或原始 `raw_occlusion_area_ratio`？当前为保持兼容，暂不增加。
- 真实遮挡正样本的召回率还未用完整集合评估。上线前建议至少跑一批人工确认的遮挡图，并保存可视化 mask。
- 生产部署设备尚未最终确定。如果服务部署在无 GPU 环境，需要接受 CPU 推理延迟，或者改为远端推理服务。

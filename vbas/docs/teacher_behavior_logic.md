# 老师行为主体与姿态判断逻辑

本文说明当前 `/ImageDetect/teacher/v1.0.0` 接口中，老师主体判断以及 `sit` / `stand` 姿态后处理逻辑。

## 模型与标签

老师行为接口只使用 `teacher_behavior.pt`。模型标签映射如下：

| 模型标签 | 内部行为键 | ObjectType | 含义 |
| --- | --- | --- | --- |
| `sit` | `sitting` | `201` | 坐着 |
| `stand` | `standing` | `202` | 站立 |
| `bbwriting` | `writing` | `203` | 板书 |
| `teach` | `teaching` | `204` | 讲授 |

接口还会输出 `ObjectType=100`，表示检测到老师主体。主体框来自最终选中主体中的代表框。

## 配置项

相关配置位于 `app/config.toml` 的 `[Teacher_Behavior_Thresd]`：

| 配置项 | 默认值 | 作用 |
| --- | --- | --- |
| `ImageSize` | `640` | `teacher_behavior.pt` 推理尺寸 |
| `sit` | `0.4` | `sit` 输出阈值，可被请求体临时覆盖 |
| `stand` | `0.4` | `stand` 输出阈值，可被请求体临时覆盖 |
| `bbwriting` | `0.25` | `bbwriting` 输出阈值，可被请求体临时覆盖 |
| `teach` | `0.25` | `teach` 输出阈值，可被请求体临时覆盖 |
| `SubjectClusterIoU` | `0.45` | 同一主体聚类 IoU 阈值 |
| `MergeIoU` | `0.8` | `SubjectClusterIoU <= 0` 时的兼容兜底值 |
| `KeepOnlyMainSubject` | `true` | 是否只保留一个主老师主体 |
| `MainSubjectStrategy` | `posture_confidence` | 主主体选择策略 |
| `PostureConflictRatio` | `0.10` | `sit` / `stand` 同时成立时的置信度差值比例阈值 |
| `PostureConflictDefault` | `stand` | 姿态冲突不明显或缺失时的默认姿态 |
| `ForcePostureWhenMissing` | `true` | 主体存在但姿态未过阈值时是否强制输出默认姿态 |

请求体中的 `Teacher_Behavior_Thresd` 可以临时覆盖 `sit`、`stand`、`bbwriting`、`teach` 四个类别阈值。未传的类别继续使用 `config.toml` 默认值。

## 头部姿态开关

`/ImageDetect/teacher/v1.0.0` 请求体支持 `ReturnHeadPose`，但它只在系统总开关开启时生效。总开关位于 `app/config.toml`：

```toml
[Teacher_Head_Pose]
Enabled = false
```

优先级如下：

| `Teacher_Head_Pose.Enabled` | `ReturnHeadPose` | 行为 |
| --- | --- | --- |
| `false` | `false` | 不加载 DirectMHP，不返回 `HeadPoseResult` |
| `false` | `true` | 不加载 DirectMHP，不返回 `HeadPoseResult`，只记录日志 |
| `true` | `false` | 不执行头部姿态检测，不返回 `HeadPoseResult` |
| `true` | `true` | 执行头部姿态检测，返回 `HeadPoseResult` |

默认 `Enabled=false`，用于保持接口默认行为、避免额外模型加载和算力开销。

## 主体判断流程

### 1. 模型推理阈值

推理时的 `conf` 使用四个类别有效阈值中的最小值：

```text
predict_conf = min(sit, stand, bbwriting, teach)
```

当前默认值下为 `0.25`。这样做的目的是保留低于 `sit/stand=0.4` 但仍可能有助于确认老师主体的姿态候选框。

### 2. 原始候选过滤

每个检测框会先经过基础过滤：

1. 检测行字段完整，至少包含 `x1, y1, x2, y2, conf, cls`。
2. 类别必须属于 `sit`、`stand`、`bbwriting`、`teach`。
3. 置信度必须大于等于 `predict_conf`。

注意：这里不是按各类别最终输出阈值过滤，而是按 `predict_conf` 过滤。最终是否输出某个标签，会在后面再按对应类别阈值判断。

### 3. 同主体聚类

候选框按 IoU 聚合为主体组：

```text
如果新框与某个已有主体组内任一框的 IoU >= SubjectClusterIoU
则归入该主体组
否则新建主体组
```

如果 `SubjectClusterIoU <= 0`，会使用 `MergeIoU` 作为兜底聚类阈值。

### 4. 主主体选择

如果 `KeepOnlyMainSubject=true`，多个主体组只保留一个。默认策略是 `posture_confidence`，排序优先级如下：

1. 是否存在姿态候选：存在 `sit` 或 `stand` 候选的主体优先。
2. 姿态候选最高置信度更高者优先。
3. 主体组内最高检测置信度更高者优先。
4. 框更靠近图像上边者优先。
5. 主体组内不同标签数量更多者优先。
6. 代表框面积更大者优先。

这意味着主主体选择不是简单取最靠上，也不是简单取最高置信度。低于最终姿态输出阈值、但高于 `predict_conf` 的 `sit/stand` 候选，也会参与主体选择，避免讲台上老师姿态略低时被其他高置信度非老师区域抢走主体。

另外仍保留两个兼容策略：

| 策略 | 说明 |
| --- | --- |
| `topmost` | 优先选择最靠近图像上边的主体 |
| `multi_label_topmost` | 优先选择标签数量更多的主体，再考虑靠上等因素 |

默认建议保持 `posture_confidence`。

## 标签输出流程

选中主体组后，会按各类别最终阈值选择每个类别的最高置信度框：

```text
类别输出条件 = 该类别最高 conf >= 该类别阈值
```

其中：

- `bbwriting` 和 `teach` 是授课行为，可以同时输出，也可以都不输出。
- `sit` 和 `stand` 是姿态，最终必须互斥处理。
- `ObjectType=100` 只要存在主体组就输出，置信度为该主体组内最高检测置信度。

## sit / stand 判断流程

### 情况 1：只有 stand 过阈值

输出 `stand`：

```text
ObjectType = 202
Confidence = stand_conf
SuspectedSitting = false
PostureFallback = false
```

### 情况 2：只有 sit 过阈值

输出 `sit`：

```text
ObjectType = 201
Confidence = sit_conf
SuspectedSitting = false
PostureFallback = false
```

### 情况 3：sit 和 stand 都过阈值

先计算置信度差值比例：

```text
diff_ratio = abs(sit_conf - stand_conf) / max(sit_conf, stand_conf)
```

如果 `diff_ratio <= PostureConflictRatio`，认为两者差距不明显，按 `PostureConflictDefault` 输出。当前默认是 `stand`：

```text
ObjectType = 202
Confidence = stand_conf
SuspectedSitting = true
PostureFallback = false
```

如果 `diff_ratio > PostureConflictRatio`，认为高置信度姿态更可信，输出置信度更高的姿态：

```text
stand_conf >= sit_conf -> 输出 stand
stand_conf < sit_conf  -> 输出 sit
```

此时：

```text
SuspectedSitting = false
PostureFallback = false
```

### 情况 4：sit 和 stand 都没有过阈值

如果选中了主体，但 `sit` / `stand` 都没有达到各自输出阈值，并且 `ForcePostureWhenMissing=true`，则强制输出 `PostureConflictDefault`。当前默认输出 `stand`：

```text
ObjectType = 202
Confidence = 主体组最高检测置信度
SuspectedSitting = false
PostureFallback = true
```

如果 `ForcePostureWhenMissing=false`，则不输出姿态。但当前业务要求结果只能是“站”或“坐”，所以默认保持 `true`。

## 响应字段说明

`ObjectPostList` 中每个位置对象除坐标外，还可能包含：

| 字段 | 说明 |
| --- | --- |
| `Confidence` | 当前输出项对应的置信度 |
| `SuspectedSitting` | 仅姿态输出使用；当 `sit/stand` 冲突不明显且默认输出 `stand` 时为 `true` |
| `PostureFallback` | 仅姿态输出使用；当主体存在但姿态未过阈值，强制输出默认姿态时为 `true` |

非姿态行为如 `bbwriting`、`teach` 一般只使用 `Confidence`，不会设置 `SuspectedSitting` 和 `PostureFallback`。

## 关键约束

1. 老师行为接口当前只使用 `teacher_behavior.pt`，旧 `teacher.pt` 姿态规则已移除。
2. 一个最终主体最多输出一个姿态：`sit` 和 `stand` 不会同时作为最终结果输出。
3. `bbwriting` 和 `teach` 可以同时输出，因为它们是授课行为，不是互斥姿态。
4. 默认只保留一个主老师主体。
5. 默认情况下，只要识别到主体，最终就会有一个姿态输出；证明不了坐，就按站处理，并通过 `PostureFallback` 标记兜底来源。

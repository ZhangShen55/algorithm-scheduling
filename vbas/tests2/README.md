# 老师面部方向离线测试

`tests2` 专门用于老师面部方向/头部姿态功能验证，不复用 `tests/` 下的旧测试图片和输出目录。

当前工具不会改动线上接口，只做离线评估：

1. 先用现有 `teacher_behavior.pt` 找到主老师主体。
2. 对主老师主体框做扩展 crop。
3. 在 crop 上调用离线放置的 DirectMHP 模型，得到头部框和 `yaw/pitch/roll`。
4. 将头部姿态转换为粗粒度面部方向。
5. 输出 CSV、JSON 和标注图，供人工评审。

## 目录约定

```text
tests2/
  images/                         # 放待测图片
  head_pose_outputs/              # 脚本输出目录
  run_teacher_head_pose_eval.py   # 离线评估脚本
  head_pose_eval_selftest.py      # 不依赖模型的后处理自测
```

DirectMHP 运行源码和默认 m 权重放在 app 内：

```text
app/vendor/DirectMHP/
app/models/cmu_m_1280_e200_t40_lw010_best.pt
```

默认数据配置文件路径：

```text
app/models/cmu_panoptic_coco.yaml
```

如果你使用 AGORA 权重或其他权重，可通过命令行参数覆盖 `--directmhp-weights` 和 `--directmhp-data`。

## DirectMHP 测试依赖

DirectMHP 是 YOLOv5 风格的旧代码，导入时依赖 `pkg_resources`。当前较新的 `setuptools` 可能不再提供该模块，因此需要安装兼容版本：

```bash
conda run -n jy-tias python -m pip install -r tests2/requirements_directmhp.txt
```

当前 `tests2/requirements_directmhp.txt` 只补离线测试脚本需要的 DirectMHP 额外依赖，不用于线上接口。

## 推荐权重

当前接口默认使用 DirectMHP-M 的 CMU-HPE 权重：

```text
cmu_m_1280_e200_t40_lw010_best.pt
```

原因：

- 你已验证 m 模型检测结果更好。
- CMU-HPE 是多人场景，和课堂远景更接近。
- DirectMHP 本身不依赖清晰眼睛区域，更适合当前教室监控场景。

## 运行命令

先运行不依赖模型的自测：

```bash
conda run -n jy-tias python -m unittest tests2.head_pose_eval_selftest
```

放入图片和模型后，运行离线评估：

```bash
conda run -n jy-tias python tests2/run_teacher_head_pose_eval.py \
  --image-dir tests2/images \
  --output-dir tests2/head_pose_outputs \
  --directmhp-root app/vendor/DirectMHP \
  --directmhp-weights app/models/cmu_m_1280_e200_t40_lw010_best.pt \
  --directmhp-data app/models/cmu_panoptic_coco.yaml \
  --device cpu
```

Mac 本地没有 NVIDIA CUDA，使用 `--device cpu`。如果在有 NVIDIA GPU 的 Linux 机器上测试，可改为：

```bash
conda run -n jy-tias python tests2/run_teacher_head_pose_eval.py --device 0
```

## 输出文件

```text
tests2/head_pose_outputs/
  images/                 # 标注图，包含老师主体框、头部框、方向标签
  crops/                  # 主老师主体 crop 图
  summary.csv             # 每张图一行的结构化结果
  response_results.json   # 拟接口响应结构示例
  run_config.json         # 本次运行配置
```

## 方向枚举

| FaceDirection | 中文含义 | 规则 |
| --- | --- | --- |
| `front` | 正面/面向学生 | `abs(yaw) <= front_yaw`，默认 `20°`；或未达到侧向阈值 |
| `left` | 向左 | `yaw <= -side_yaw`，默认 `-25°` |
| `right` | 向右 | `yaw >= side_yaw`，默认 `25°` |
| `down` | 低头 | `pitch >= down_pitch`，默认 `25°` |
| `board` | 看黑板/板书方向 | 检测到 `bbwriting` 且 `abs(yaw) >= board_yaw`，默认 `35°` |
| `unknown` | 未知 | 未检测到老师主体、头部或姿态 |

阈值可以通过命令行参数覆盖：

```bash
--front-yaw 20 --side-yaw 25 --down-pitch 25 --board-yaw 35
```

## 拟响应字段解释

`response_results.json` 中每张图会生成一个 `TeacherFaceDirection` 对象。字段含义如下：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `FaceDirection` | string | 粗粒度方向枚举：`front`、`left`、`right`、`down`、`board`、`unknown` |
| `FaceDirectionName` | string | 中文方向名称 |
| `DirectionReason` | string | 当前方向的判断原因 |
| `Yaw` | number/null | 头部 yaw 角度，单位度；负值偏左，正值偏右 |
| `Pitch` | number/null | 头部 pitch 角度，单位度；当前规则中较大正值按低头处理 |
| `Roll` | number/null | 头部 roll 角度，单位度；暂不参与方向分类 |
| `HeadPoseConfidence` | number/null | DirectMHP 头部检测置信度 |
| `TeacherConfidence` | number/null | `teacher_behavior.pt` 主老师主体置信度 |
| `IsWriting` | bool | 当前主老师主体是否输出了 `bbwriting` |
| `IsTeaching` | bool | 当前主老师主体是否输出了 `teach` |
| `TeacherSubjectBox` | object | 主老师主体框，原图坐标 |
| `HeadBox` | object | 头部框，原图坐标 |
| `Source` | string | 结果来源，当前为 `teacher_behavior.pt + DirectMHP` |

`Status` 字段解释：

| Status | 含义 |
| --- | --- |
| `success` | 成功检测到老师主体和头部姿态 |
| `no_teacher` | 未检测到老师主体 |
| `no_head` | 检测到老师主体，但 DirectMHP 未检测到头部 |
| `failed_read` | 图片读取失败 |

## 评审重点

第一轮不要直接看“注意力准确率”，先看这些基础问题：

1. 老师主体是否选对。
2. crop 是否覆盖老师头部。
3. DirectMHP 是否能稳定检测老师头部。
4. `yaw/pitch/roll` 在同类画面中是否稳定。
5. `bbwriting + yaw` 是否能支撑 `board` 判断。
6. 是否被学生、幕布图片、屏幕内容中的人脸干扰。

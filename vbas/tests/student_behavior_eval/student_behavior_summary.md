# 学生行为检测评估

- 输入目录: `/Users/zhangshen/Documents/workspace/jy-algorithm-tias-server/tests/tests_data/学生行为`
- 输出目录: `/Users/zhangshen/Documents/workspace/jy-algorithm-tias-server/tests/student_behavior_eval`
- 图片数量: 110
- 原始候选框最低 conf: 0.1

## 当前配置阈值

| 标签 | 阈值 |
| --- | ---: |
| 使用手机 (Using_phone) | 0.300 |
| 举手 (Hand_raising) | 0.750 |
| 睡觉 (Sleep) | 0.150 |
| 站立 (standing) | 0.200 |
| 阅读 (Read_W) | 0.400 |

## 原始候选框数量

| 标签 | 数量 |
| --- | ---: |
| 使用手机 (Using_phone) | 890 |
| 举手 (Hand_raising) | 204 |
| 睡觉 (Sleep) | 86 |
| 站立 (standing) | 219 |
| 阅读 (Read_W) | 381 |

## 按配置过滤后数量

| 标签 | 数量 |
| --- | ---: |
| 使用手机 (Using_phone) | 675 |
| 举手 (Hand_raising) | 8 |
| 睡觉 (Sleep) | 83 |
| 站立 (standing) | 140 |
| 阅读 (Read_W) | 203 |

## 原始候选框置信度分布

| 标签 | count | min | p25 | p50 | p75 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 使用手机 (Using_phone) | 890 | 0.1001 | 0.3120 | 0.6378 | 0.8061 | 0.8890 |
| 举手 (Hand_raising) | 204 | 0.1000 | 0.1464 | 0.2408 | 0.3866 | 0.8116 |
| 睡觉 (Sleep) | 86 | 0.1128 | 0.4879 | 0.5971 | 0.6557 | 0.7458 |
| 站立 (standing) | 219 | 0.1013 | 0.1620 | 0.3182 | 0.7444 | 0.8436 |
| 阅读 (Read_W) | 381 | 0.1012 | 0.2124 | 0.4480 | 0.7335 | 0.8981 |

## 文件说明

- `raw_all_labels/`: 模型原始候选框标注图，最低 conf 与线上推理入口一致为 0.1。
- `filtered_by_config/`: 按 `tias/config.toml` 的 `[Student_Thresd]` 过滤后的标注图。
- `student_behavior_raw_detections.csv`: 原始候选框明细。
- `student_behavior_filtered_detections.csv`: 过滤后明细。

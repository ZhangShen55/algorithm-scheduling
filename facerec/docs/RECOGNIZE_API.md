# 识别接口

所有业务响应的 HTTP 状态均为 200，调用方通过响应体 `status_code` 判断业务结果。

## POST /recognize

请求字段保持兼容，仅包含：

```json
{
  "photo": "data:image/jpeg;base64,...",
  "targets": ["T001", "T002"],
  "threshold": 0.4
}
```

- `photo`：必填，Base64 Data URL。
- `targets`：可选，优先候选人员编号列表。
- `threshold`：可选，全库匹配阈值。
- 不支持 `points` 或 ROI 字段。

InsightFace 会处理整图内所有满足置信度和尺寸条件的人脸。响应中的 `bbox` 保持旧合同，
表示面积最大的主脸；`match` 汇总全部有效人脸的匹配结果，按 `number` 去重并按最高相似度
降序排列。

```json
{
  "status_code": 200,
  "message": "识别成功",
  "data": {
    "has_face": true,
    "bbox": {"x": 100, "y": 120, "w": 150, "h": 150},
    "threshold": 0.4,
    "match": [
      {
        "id": "507f1f77bcf86cd799439011",
        "name": "示例人员",
        "number": "T001",
        "similarity": "87.45%",
        "is_target": true
      }
    ],
    "message": "匹配成功"
  }
}
```

兼容字段必须保持 `status_code`、`has_face`、`bbox`、`match`，不得改为驼峰字段或
`bboxs`。

## POST /recognize/batch

批量接口继续表示同一人员的多帧聚合，不表示单张图片中的多人脸：

```json
{
  "photos": [
    "data:image/jpeg;base64,...",
    "data:image/jpeg;base64,..."
  ],
  "targets": ["T001"],
  "threshold": 0.4
}
```

响应 `data` 保持 `total_frames`、`valid_frames`、`threshold`、`frames`、`match` 和
`message`。

## 业务状态码

| `status_code` | 含义 |
| --- | --- |
| `200` | 成功且存在匹配 |
| `201` | 未检测到有效人脸 |
| `202` | 人脸尺寸过小 |
| `251` | 人员库为空 |
| `252` | 有效人脸未达到匹配阈值 |
| `400` | 请求参数错误 |
| `401` | Base64 解码失败 |
| `402` | 图片格式错误 |
| `403` | 图片数据无效 |
| `500` | 内部错误 |
| `501` | 检测失败 |
| `502` | embedding 提取失败 |

FaceRec 只处理请求携带的图片，不访问 RTSP、视频源或额外帧。

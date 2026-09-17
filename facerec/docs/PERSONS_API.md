# 人员管理接口

人员记录和 512 维 embedding 只存储在 MongoDB。FaceRec 不使用 Redis 人员缓存。所有业务
响应的 HTTP 状态均为 200，通过响应体 `status_code` 判断结果。

## POST /persons

```json
{
  "name": "示例人员",
  "number": "T001",
  "photo": "data:image/jpeg;base64,..."
}
```

`number` 不存在时创建记录，已存在时更新同一记录。成功响应：

```json
{
  "status_code": 200,
  "message": "人物特征创建成功",
  "data": {
    "id": "507f1f77bcf86cd799439011",
    "name": "示例人员",
    "number": "T001",
    "photo_path": "",
    "tip": "人脸特征像素正常，可以使用"
  }
}
```

默认 `image.save_person_photo=false`。此时仍生成并写入非空 512 维 embedding，
`photo_path` 为空，且不会在 `media/person_photos/` 写入人脸原图。

## POST /persons/batch

```json
{
  "persons": [
    {"name": "示例 A", "number": "T001", "photo": "data:image/jpeg;base64,..."},
    {"name": "示例 B", "number": "T002", "photo": "data:image/jpeg;base64,..."}
  ]
}
```

- `status_code=200`：全部成功。
- `status_code=207`：部分成功，`data` 含成功/失败数量、失败编号和逐项结果。
- `status_code=400`：全部失败。

批量接口逐条提交 MongoDB，一条失败不会回滚已成功记录。

## GET /persons

查询参数为 `skip` 和 `limit`，成功时 `data.persons` 返回分页记录，不包含 embedding。

## POST /persons/search

请求可包含 `name`、`number` 或二者。`name` 为不区分大小写的模糊匹配，`number` 为精确
匹配；至少提供一个字段。响应不包含 embedding。

## DELETE /persons/delete

```json
{"number": "T001"}
```

请求可按 `name`、`number` 或 `id` 删除，优先级依次为 `name`、`number`、`id`。`name`
沿用旧合同的模糊删除语义，生产调用优先使用精确的 `number` 或 `id`。

新增、更新或删除成功后，其他 FaceRec 实例通过共享 MongoDB 直接观察最新事实，无需执行
算子侧 cache 失效。

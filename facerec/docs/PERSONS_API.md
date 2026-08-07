# Persons API 文档

人脸特征管理接口的完整说明文档。

---

## 📋 目录

- [重要说明](#重要说明)
- [接口概览](#接口概览)
- [统一响应格式](#统一响应格式)
- [状态码说明](#状态码说明)
- [创建人物](#创建人物)
- [批量创建](#批量创建)
- [查询人物](#查询人物)
- [搜索人物](#搜索人物)
- [删除人物](#删除人物)
- [最佳实践](#最佳实践)

---

## 重要说明

**🔥 v5.0 重大变更**

从 v5.0 开始，所有接口遵循以下规则：

1. **HTTP 状态码永远是 200** - 不再抛出 HTTP 异常
2. **通过 `status_code` 字段判断结果** - 成功/失败都在响应体中
3. **统一响应格式** - 所有接口返回相同结构：`{status_code, message, data}`
4. **参数验证增强** - 缺少必填参数时返回友好的中文错误提示（如"缺少name参数"）
5. **批量接口优化** - `/persons/batch` 接口在循环中验证参数，即使部分记录参数缺失，其他有效记录仍会继续处理并返回 207 状态码

**前端/客户端适配要点：**
```javascript
// ❌ 旧方式（v4.0 及之前）
try {
  const response = await fetch('/persons', {...});
  if (!response.ok) throw new Error('HTTP error');
  const data = await response.json();
} catch (error) {
  // 处理 HTTP 异常
}

// ✅ 新方式（v5.0）
const response = await fetch('/persons', {...});
const result = await response.json();  // HTTP 永远是 200

if (result.status_code === 200) {
  // 成功 - 使用 result.data
  console.log(result.data);
} else {
  // 失败 - 显示 result.message
  console.error(result.message);
}
```

---

## 接口概览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/persons` | 创建或更新单个人物特征 |
| POST | `/persons/batch` | 批量创建或更新人物特征 |
| GET | `/persons` | 获取人物列表（分页） |
| POST | `/persons/search` | 搜索人物（支持模糊/精确） |
| DELETE | `/persons/delete` | 通用删除接口（支持 name/number/id） |

---

## 统一响应格式

**所有接口**都返回以下格式（HTTP 状态码永远是 200）：

```json
{
  "status_code": 200,
  "message": "操作成功",
  "data": {
    // 具体数据，根据接口不同而不同
  }
}
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| status_code | int | 业务状态码（200=成功，400/404/422/500/502=各种失败） |
| message | string | 操作结果描述信息 |
| data | object/null | 成功时包含具体数据，失败时可能为 null 或包含错误详情 |

---

## 状态码说明

| status_code | 含义 | 适用场景 |
|-------------|------|---------|
| **200** | 成功 | 操作成功完成 |
| **207** | 部分成功 | 批量处理时部分成功、部分失败 |
| **400** | 请求参数错误 | 缺少必填参数、图片数据无效、批量处理全部失败 |
| **404** | 资源未找到 | 数据库为空、未找到匹配的人物 |
| **422** | 无法处理的实体 | 未检测到人脸 |
| **423** | 人脸尺寸过小 | 检测到的人脸过小，无法识别 |
| **500** | 服务器内部错误 | 数据库操作失败 |
| **501** | 人脸检测服务错误 | 人脸检测服务内部异常 |
| **502** | 特征提取失败 | 人脸特征提取服务异常 |
| **503** | 文件保存失败 | 文件系统错误 |

---

## 创建人物

### `POST /persons`

创建或更新单个人物的人脸特征。

**功能说明**：
- 如果 `number` 已存在，则更新该人物信息
- 如果 `number` 不存在，则创建新人物
- 自动检测人脸、提取特征、保存图片

**请求体**：

```json
{
  "name": "张三",
  "number": "001",
  "photo": "data:image/jpeg;base64,/9j/4AAQSkZJRg..."
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| name | string | 是 | 人物姓名（不能为空或只包含空格） |
| number | string | 是 | 人物编号（唯一标识，不能为空或只包含空格） |
| photo | string | 是 | Base64编码的图片数据（不能为空或只包含空格） |

**⚠️ 参数验证说明**：
- 所有字段都是必填的，缺少任何一个字段或字段值为空字符串/只包含空格时，将返回 `status_code=400` 的错误响应
- 错误信息格式：`"缺少{字段名}参数"`（如："缺少name参数"、"缺少number参数"、"缺少photo参数"）

**成功响应** (status_code=200):

```json
{
  "status_code": 200,
  "message": "人物特征创建成功",
  "data": {
    "id": "507f1f77bcf86cd799439011",
    "name": "张三",
    "number": "001",
    "photo_path": "/media/person_photos/张三_001_a3b4c5d6.jpg",
    "tip": "人脸特征像素正常，可以使用"
  }
}
```

**错误响应示例**：

| status_code | message | 原因 |
|-------------|---------|------|
| 400 | 缺少name参数 | 请求体中未提供 name 字段 |
| 400 | 缺少number参数 | 请求体中未提供 number 字段 |
| 400 | 缺少photo参数 | 请求体中未提供 photo 字段 |
| 400 | base64 解码失败: Incorrect padding | base64 格式错误 |
| 400 | 未接收到有效图片数据或图像数据存在异常 | 图片格式错误或损坏 |
| 422 | 未检测到有效人脸 | 图片中没有人脸 |
| 423 | 检测到的人脸过小(小于80*80px)，无法识别，请重新捕捉人脸 | 人脸尺寸不足 |
| 501 | 人脸检测服务内部错误: ... | 检测服务异常 |
| 502 | 人脸特征提取失败: ... | 特征提取失败 |
| 503 | 人物特征创建成功，但图片保存失败 | 文件系统错误（**注意：此错误会返回 data，因为核心业务已成功**） |
| 500 | 数据库操作失败: ... | 数据库异常 |

**错误响应格式：**

参数缺失错误示例：
```json
{
  "status_code": 400,
  "message": "缺少name参数",
  "data": null
}
```

人脸检测失败示例：
```json
{
  "status_code": 422,
  "message": "未检测到有效人脸",
  "data": null
}
```

文件保存失败示例（**注意：此错误会返回 data**）：
```json
{
  "status_code": 503,
  "message": "人物特征创建成功，但图片保存失败",
  "data": {
    "id": "507f1f77bcf86cd799439011",
    "name": "张三",
    "number": "001",
    "photo_path": "",
    "tip": "人脸特征像素正常，可以使用 (图片保存失败)"
  }
}
```

**说明**：当文件保存失败时，由于人脸检测、特征提取和数据库保存都已成功，API 会返回完整的数据信息（photo_path 为空字符串），客户端可以正常使用该记录进行人脸识别，只是无法查看原始照片。

---

## 批量创建

### `POST /persons/batch`

批量上传多个人物的人脸特征。

**功能说明**：
- 接受多个人物信息
- **参数验证在循环中进行**：即使部分记录参数缺失，其他有效记录仍会继续处理
- **全部成功**：返回 status_code=200
- **部分失败**：返回 status_code=207，data 中包含详细信息（包括参数验证失败的记录）
- **全部失败**：返回 status_code=400，data 中包含详细信息

**请求体**：

```json
{
  "persons": [
    {
      "name": "张三",
      "number": "001",
      "photo": "data:image/jpeg;base64,/9j/4AAQ..."
    },
    {
      "name": "李四",
      "number": "002",
      "photo": "data:image/jpeg;base64,/9j/4AAQ..."
    }
  ]
}
```

**全部成功响应** (status_code=200):

```json
{
  "status_code": 200,
  "message": "批量处理成功: 2条",
  "data": {
    "persons": [
      {
        "id": "507f1f77bcf86cd799439011",
        "name": "张三",
        "number": "001",
        "photo_path": "/media/person_photos/张三_001_a3b4c5d6.jpg",
        "tip": "人脸特征像素正常，可以使用"
      },
      {
        "id": "507f1f77bcf86cd799439012",
        "name": "李四",
        "number": "002",
        "photo_path": "/media/person_photos/李四_002_b7c8d9e0.jpg",
        "tip": "人脸特征像素正常，可以使用"
      }
    ]
  }
}
```

**部分失败响应** (status_code=207):

**示例1：参数验证失败 + 处理成功混合**
```json
{
  "status_code": 207,
  "message": "批量处理部分失败: 成功1条，失败2条",
  "data": {
    "success_count": 1,
    "failed_count": 2,
    "failed_numbers": ["001", "002"],
    "failed_details": [
      "第1个人物: 缺少name参数",
      "第2个人物(李四): 缺少number参数"
    ],
    "persons": [
      {
        "id": "",
        "name": "",
        "number": "001",
        "photo_path": "",
        "tip": "错误: 缺少name参数"
      },
      {
        "id": "",
        "name": "李四",
        "number": "",
        "photo_path": "",
        "tip": "错误: 缺少number参数"
      },
      {
        "id": "507f1f77bcf86cd799439013",
        "name": "王五",
        "number": "005",
        "photo_path": "/media/person_photos/王五_005_c9d0e1f2.jpg",
        "tip": "人脸特征像素正常，可以使用"
      }
    ]
  }
}
```

**示例2：人脸检测失败**
```json
{
  "status_code": 207,
  "message": "批量处理部分失败: 成功1条，失败2条",
  "data": {
    "success_count": 1,
    "failed_count": 2,
    "failed_numbers": ["002", "003"],
    "failed_details": [
      "第2个人物(李四_002): 未检测到有效人脸",
      "第3个人物(王五_003): 检测人脸特征尺寸过小"
    ],
    "persons": [
      {
        "id": "507f1f77bcf86cd799439011",
        "name": "张三",
        "number": "001",
        "photo_path": "/media/person_photos/张三_001_a3b4c5d6.jpg",
        "tip": "人脸特征像素正常，可以使用"
      },
      {
        "id": "",
        "name": "李四",
        "number": "002",
        "photo_path": "",
        "tip": "错误: 未检测到有效人脸"
      },
      {
        "id": "",
        "name": "王五",
        "number": "003",
        "photo_path": "",
        "tip": "错误: 检测人脸特征尺寸过小"
      }
    ]
  }
}
```

**全部失败响应** (status_code=400):

```json
{
  "status_code": 400,
  "message": "批量处理全部失败: 3条",
  "data": {
    "success_count": 0,
    "failed_count": 3,
    "failed_numbers": ["001", "002", "003"],
    "failed_details": [
      "第1个人物(张三_001): 未检测到有效人脸",
      "第2个人物(李四_002): 未检测到有效人脸",
      "第3个人物(王五_003): 检测人脸特征尺寸过小"
    ],
    "persons": [
      {
        "id": "",
        "name": "张三",
        "number": "001",
        "photo_path": "",
        "tip": "错误: 未检测到有效人脸"
      },
      {
        "id": "",
        "name": "李四",
        "number": "002",
        "photo_path": "",
        "tip": "错误: 未检测到有效人脸"
      },
      {
        "id": "",
        "name": "王五",
        "number": "003",
        "photo_path": "",
        "tip": "错误: 检测人脸特征尺寸过小"
      }
    ]
  }
}
```

**data 字段说明（失败时）**：

| 字段 | 类型 | 说明 |
|------|------|------|
| success_count | int | 成功入库的数量 |
| failed_count | int | 失败的数量 |
| failed_numbers | array | 失败记录的编号列表 |
| failed_details | array | 失败详情（最多5条） |
| persons | array | 所有记录的处理结果，失败记录的 id 为空字符串 |

**判断方法**：
- ✅ **全部成功**：`status_code === 200`
- ⚠️ **部分失败**：`status_code === 207`（部分成功、部分失败）
- ❌ **全部失败**：`status_code === 400`（所有记录都失败）

---

### 批量接口失败类型详解

批量接口在处理每个人物记录时，可能遇到以下 **11 种失败类型**，归为 6 大类：

#### 1. 参数验证失败（3种）

| 失败类型 | failed_details 格式 | tip 格式 |
|---------|---------------------|----------|
| 缺少name参数 | `"第{idx}个人物: 缺少name参数"` | `"错误: 缺少name参数"` |
| 缺少number参数 | `"第{idx}个人物({name}): 缺少number参数"` | `"错误: 缺少number参数"` |
| 缺少photo参数 | `"第{idx}个人物({name}_{number}): 缺少photo参数"` | `"错误: 缺少photo参数"` |

#### 2. 图片处理失败（2种）

| 失败类型 | failed_details 格式 | tip 格式 |
|---------|---------------------|----------|
| Base64解码失败 | `"第{idx}个人物({name}_{number}): base64 解码失败: Incorrect padding"` | `"错误: base64 解码失败: Incorrect padding"` |
| 未接收到有效图片数据 | `"第{idx}个人物({name}_{number}): 未接收到有效图片数据或图像数据存在异常"` | `"错误: 未接收到有效图片数据或图像数据存在异常"` |

#### 3. 人脸检测失败（3种）

| 失败类型 | failed_details 格式 | tip 格式 |
|---------|---------------------|----------|
| 人脸检测服务内部错误 | `"第{idx}个人物({name}_{number}): 人脸检测服务内部错误 - {error}"` | `"错误: 人脸检测服务内部错误 - {error}"` |
| 未检测到有效人脸 | `"第{idx}个人物({name}_{number}): 未检测到有效人脸"` | `"错误: 未检测到有效人脸"` |
| 检测人脸尺寸过小 | `"第{idx}个人物({name}_{number}): 检测人脸特征尺寸过小"` | `"错误: 检测到的人脸过小小于80*80px"` |

#### 4. 特征提取失败（1种）

| 失败类型 | failed_details 格式 | tip 格式 |
|---------|---------------------|----------|
| 人脸特征提取失败 | `"第{idx}个人物({name}_{number}): 人脸特征提取失败 - {error}"` | `"错误: 人脸特征提取失败 - {error}"` |

#### 5. 数据库操作失败（1种）

| 失败类型 | failed_details 格式 | tip 格式 |
|---------|---------------------|----------|
| 数据库操作失败 | `"第{idx}个人物({name}_{number}): 数据库操作失败 - {error}"` | `"错误: 数据库操作失败 - {error}"` |

#### 6. 未知异常（1种）

| 失败类型 | failed_details 格式 | tip 格式 |
|---------|---------------------|----------|
| 处理异常（兜底） | `"第{idx}个人物处理异常: {error}"` | `"错误: 处理异常 - {error}"` |

**说明**：
- 所有失败的记录在 `persons` 数组中的 `id` 字段为空字符串 `""`
- `failed_details` 最多返回前 5 条错误详情
- 每个失败记录的 `tip` 字段包含错误描述，客户端可直接显示给用户
- 参数验证在循环开始时进行，即使部分记录参数缺失，其他有效记录仍会继续处理

---

## 查询人物

### `GET /persons`

获取人物列表，支持分页。

**查询参数**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| skip | int | 0 | 跳过的记录数 |
| limit | int | 100 | 返回的记录数（最大100） |

**请求示例**：

```
GET /persons?skip=0&limit=20
```

**成功响应** (status_code=200):

```json
{
  "status_code": 200,
  "message": "查询成功",
  "data": {
    "persons": [
      {
        "id": "507f1f77bcf86cd799439011",
        "name": "张三",
        "number": "001",
        "photo_path": "/media/person_photos/张三_001_a3b4c5d6.jpg",
        "bbox": "100,50,200,250",
        "tip": "人脸特征像素正常，可以使用"
      },
      {
        "id": "507f1f77bcf86cd799439012",
        "name": "李四",
        "number": "002",
        "photo_path": "/media/person_photos/李四_002_b7c8d9e0.jpg",
        "bbox": "120,60,180,230",
        "tip": ""
      }
    ]
  }
}
```

**数据库为空响应** (status_code=404):

```json
{
  "status_code": 404,
  "message": "数据库为空，请先创建人物",
  "data": null
}
```

---

## 搜索人物

### `POST /persons/search`

根据姓名或编号搜索人物。

**功能说明**：
- 只传 `name`: 模糊查询（支持部分匹配）
- 只传 `number`: 精确查询
- 都传: 组合查询 (name模糊 AND number精确)

**请求体**：

```json
{
  "name": "张",
  "number": "001"
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| name | string | 否 | 姓名（模糊匹配，忽略大小写） |
| number | string | 否 | 编号（精确匹配） |

**注意**：至少提供一个参数

**找到结果响应** (status_code=200):

```json
{
  "status_code": 200,
  "message": "搜索成功，找到 1 条记录",
  "data": {
    "persons": [
      {
        "id": "507f1f77bcf86cd799439011",
        "name": "张三",
        "number": "001",
        "photo_path": "/media/person_photos/张三_001_a3b4c5d6.jpg",
        "bbox": "100,50,200,250",
        "tip": "人脸特征像素正常，可以使用"
      }
    ]
  }
}
```

**未找到结果响应** (status_code=200):

```json
{
  "status_code": 200,
  "message": "未找到符合条件的人物",
  "data": {
    "persons": []
  }
}
```

**参数错误响应** (status_code=400):

```json
{
  "status_code": 400,
  "message": "name 和 number 至少提供一个",
  "data": null
}
```

---

## 删除人物

### `DELETE /persons/delete`

通用删除接口,支持按 `number`(精确)、`name`(模糊) 或 `id`(精确) 删除。

**请求体**：

按 `number` 精确删除（推荐）:
```json
{
  "number": "001"
}
```

按 `name` 模糊删除:
```json
{
  "name": "张三"
}
```

按 `id` 删除（向后兼容）:
```json
{
  "id": "507f1f77bcf86cd799439011"
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| number | string | 否 | 人物编号（**精确匹配，推荐**） |
| name | string | 否 | 姓名（**模糊匹配，不区分大小写**） |
| id | string | 否 | MongoDB文档ID（精确匹配） |

**⚠️ 重要说明**：
- **至少提供一个参数**
- **优先级**: `name` > `number` > `id`
- **推荐使用 `number` 参数** - 精确匹配，不会误删
- **name 参数使用模糊匹配**（MongoDB `$regex`）
  - 例如：`name: "张三"` 会删除"张三"、"张三丰"、"小张三"等所有包含"张三"的记录
  - 可能一次删除多条记录

**成功响应** (status_code=200):

按 `name` 模糊删除（可能删除多条）:
```json
{
  "status_code": 200,
  "message": "成功删除 2 个人物",
  "data": {
    "deleted_count": 2,
    "info": [
      {
        "id": "507f1f77bcf86cd799439011",
        "number": "t123",
        "name": "张三"
      },
      {
        "id": "507f1f77bcf86cd799439012",
        "number": "t124",
        "name": "张三丰"
      }
    ]
  }
}
```

按 `number` 精确删除（只删除一条,但返回数组格式）:
```json
{
  "status_code": 200,
  "message": "成功删除 1 个人物",
  "data": {
    "deleted_count": 1,
    "info": [
      {
        "id": "507f1f77bcf86cd799439011",
        "number": "t123",
        "name": "张三"
      }
    ]
  }
}
```

**未找到响应** (status_code=404):

```json
{
  "status_code": 404,
  "message": "未找到匹配人物",
  "data": null
}
```

**参数错误响应** (status_code=400):

```json
{
  "status_code": 400,
  "message": "name、number 和 id 至少提供一个",
  "data": null
}
```

---

## 最佳实践

### 1. 统一的错误处理

```javascript
async function callApi(url, options) {
  const response = await fetch(url, options);
  const result = await response.json();  // HTTP 永远是 200

  if (result.status_code === 200) {
    return result.data;  // 成功，返回数据
  } else {
    throw new Error(result.message);  // 失败，抛出业务错误
  }
}
```

### 2. 创建人物

```javascript
async function createPerson(name, number, photoBase64) {
  try {
    const data = await callApi('/persons', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, number, photo: photoBase64 })
    });

    console.log('创建成功:', data);
    return data;
  } catch (error) {
    console.error('创建失败:', error.message);
    throw error;
  }
}
```

### 3. 批量创建（带失败处理）

```javascript
async function batchCreatePersons(persons) {
  const response = await fetch('/persons/batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ persons })
  });

  const result = await response.json();

  if (result.status_code === 200) {
    // 全部成功
    console.log('全部成功:', result.data.persons);
    return { success: true, data: result.data };
  } else if (result.status_code === 207) {
    // 部分失败
    console.warn(`部分失败: 成功${result.data.success_count}条，失败${result.data.failed_count}条`);
    console.warn('失败编号:', result.data.failed_numbers);
    console.warn('失败详情:', result.data.failed_details);

    // 可以选择只返回成功的记录
    const successRecords = result.data.persons.filter(p => p.id !== "");
    return { success: false, partial: true, data: result.data, successRecords };
  } else if (result.status_code === 400) {
    // 全部失败
    console.error('全部失败:', result.message);
    console.error('失败详情:', result.data.failed_details);
    return { success: false, partial: false, data: result.data };
  } else {
    throw new Error(result.message);
  }
}
```

### 4. 搜索人物

```javascript
async function searchPerson(keyword) {
  const response = await fetch('/persons/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: keyword })
  });

  const result = await response.json();

  if (result.status_code === 200) {
    return result.data.persons;  // 返回结果数组（可能为空）
  } else {
    throw new Error(result.message);
  }
}
```

### 5. 按编号精确删除（推荐）

```javascript
async function deleteByNumber(number) {
  const response = await fetch('/persons/delete', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ number })
  });

  const result = await response.json();

  if (result.status_code === 200) {
    console.log(result.message);  // "成功删除 1 个人物"
    console.log('删除的人物:', result.data.info);
    return result.data;
  } else if (result.status_code === 404) {
    console.warn('未找到该人物');
    return null;
  } else {
    throw new Error(result.message);
  }
}
```

### 6. 按姓名模糊删除（慎用）

```javascript
async function deleteByName(name) {
  // 警告：模糊匹配可能删除多个人物
  const confirmation = confirm(`确定要删除所有名字包含"${name}"的人物吗？`);
  if (!confirmation) return;

  const response = await fetch('/persons/delete', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  });

  const result = await response.json();

  if (result.status_code === 200) {
    console.log(`成功删除 ${result.data.deleted_count} 个人物`);
    console.log('删除的人物列表:', result.data.info);
    return result.data;
  } else if (result.status_code === 404) {
    console.warn('未找到匹配的人物');
    return null;
  } else {
    throw new Error(result.message);
  }
}
```

### 7. React 示例（使用 hooks）

```javascript
import { useState } from 'react';

function PersonManager() {
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const handleCreatePerson = async (name, number, photo) => {
    setError(null);

    const response = await fetch('/persons', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, number, photo })
    });

    const data = await response.json();

    if (data.status_code === 200) {
      setResult(data.data);
      alert('创建成功！');
    } else {
      setError(data.message);
      alert(`创建失败: ${data.message}`);
    }
  };

  return (
    <div>
      {error && <div className="error">{error}</div>}
      {result && <div className="success">ID: {result.id}</div>}
      {/* ... 表单组件 */}
    </div>
  );
}
```

---

## 图片要求

**支持格式**：JPG, PNG, BMP

**尺寸要求**：
- 最小人脸尺寸：80x80 像素
- 推荐图片分辨率：≥ 640x480

**质量要求**：
- 人脸清晰可见
- 光线充足
- 正面或接近正面角度
- 无遮挡（口罩、墨镜等）

---

## 更新日志

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v5.0 | 2026-01-13 | **重大变更**: 统一响应格式，所有接口 HTTP 状态码永远返回 200，通过 `status_code` 字段区分成功/失败。移除所有 HTTPException。这是一个**破坏性变更**，需要客户端/前端适配。 |
| v4.0 | 2026-01-12 | **破坏性变更**: 移除冗余接口 `/persons/by_name` 和 `/persons/by_id`,统一使用 `/persons/delete` |
| v3.2 | 2026-01-12 | **重要优化**: 统一所有删除接口的响应格式,`info` 字段始终返回数组,避免类型不一致问题 |
| v3.1 | 2026-01-12 | 优化 `/persons/delete` 接口：支持按 `number` 精确删除，返回详细的删除信息（包含 id、number 和 name） |
| v3.0 | 2026-01-12 | 完全重写文档，基于最新代码更新所有接口说明 |
| v2.1 | 2026-01-09 | 补充通用删除接口说明与响应示例 |
| v2.0 | 2026-01-07 | 优化删除接口，添加安全警告 |
| v1.0 | - | 初始版本 |

---

## 相关文档

- [RECOGNIZE_API.md](./RECOGNIZE_API.md) - 人脸识别接口文档
- [DEPLOYMENT.md](./DEPLOYMENT.md) - 部署说明文档

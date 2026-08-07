# FaceRecAPI 人脸对比服务

基于 FastAPI + Dlib + ArcFace(ONNX/FastDeploy) 的人脸对比系统，提供人员库管理、单图识别、多帧识别、运维统计与简单 Web 管理界面。

## Git 仓库

- https://github.com/ZhangShen55/FaceRec

## 项目特点

- Dlib 68 点关键点检测 + 5 点对齐，提升特征稳定性
- ArcFace 512 维特征向量，余弦相似度匹配
- 支持 targets 优先匹配策略（阈值放宽，提高召回）
- 多帧识别聚合，提高视频流/抓拍稳定性
- API 统计中间件 + MongoDB TTL 自动清理

## 识别流程（/recognize）

1. Base64 图片解析与像素校验（尺寸、大小）
2. Dlib 进程池检测最大人脸并对齐到 112x112
3. ArcFace 提取 512 维向量并归一化
4. 与库中向量做余弦相似度（点积）
5. 先做全局匹配，再对 targets 做阈值放宽匹配并合并去重

## 目录结构

```
app/
├── main.py                # FastAPI 入口与生命周期管理
├── core/                  # 配置、数据库、AI 引擎、日志
├── router/                # API 路由（faces/persons/ops/web）
├── services/              # 业务服务层（person/ops/face）
├── middleware/            # 统计中间件
├── models/                # 请求/响应模型
├── utils/                 # 图片解析与校验
├── ai_models/             # Dlib/ArcFace 模型文件
├── static/                # Web UI 静态资源
├── media/                 # 人脸裁剪图保存目录
├── logs/                  # 日志输出
└── docs/                  # 详细 API 文档
```

## 快速开始

### 1) 环境要求

- Python 3.8+
- MongoDB 4.4+
- Dlib 编译依赖（cmake, libboost, libopencv）
- GPU 可选（使用 fastdeploy-gpu-python）

### 2) 安装依赖

```bash
pip install -r app/requirements.txt
```

### 3) 准备模型文件

将模型文件放入 `app/ai_models/`：

- `shape_predictor_68_face_landmarks.dat`
- `ms1mv3_arcface_r100.onnx`

参考：`app/ai_models/README.md`

### 4) 配置

编辑 `app/config.toml`（按实际环境修改）：

```toml
[db]
username = "root"
password = "root"
host = "10.80.5.25"
port = "27017"
database = "facerecapi"
auth_source = "admin"
limit = 5000

[face]
threshold = 0.4
candidate_threshold = 0.2
rec_min_face_hw = 50

[threading]
max_workers = 2

[frontlogin]
username = "admin"
password = "admin"

[image]
max_feature_image_width_px = 9999
max_feature_image_height_px = 9999
min_feature_image_width_px = 80
min_feature_image_height_px = 80
max_feature_image_size_m = 10
max_face_hw = 999
min_face_hw = 50

[stats]
retention_days = 7
hourly_retention_days = 30
```

### 5) 启动服务

在项目根目录执行：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8003
```

Swagger UI: `http://localhost:8003/docs`

## API 速览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/persons` | 新增或更新人物（按 number 去重） |
| POST | `/persons/batch` | 批量新增/更新人物 |
| GET | `/persons` | 人物列表（分页） |
| POST | `/persons/search` | 搜索人物（name 模糊/number 精确） |
| DELETE | `/persons/delete` | 通用删除（name/number/id） |
| POST | `/recognize` | 单图识别 |
| POST | `/recognize/batch` | 多帧识别并聚合 |
| GET | `/ops/health` | 健康检查 |
| GET | `/ops/metrics` | 系统指标 |
| GET | `/ops/stats/api-calls` | API 调用明细 |
| GET | `/ops/stats/hourly` | 按小时统计 |
| GET | `/ops/stats/summary` | 汇总统计 |
| GET | `/` | Web 管理页（HTTP Basic） |

详细接口文档：

- `app/docs/PERSONS_API.md`
- `app/docs/RECOGNIZE_API_ERRORS.md`
- `app/docs/OPS_API.md`

## 请求示例

### 1) 添加人物

```json
POST /persons
{
  "name": "张三",
  "number": "T001",
  "photo": "data:image/jpeg;base64,/9j/4AAQSkZJRg..."
}
```

### 2) 单图识别

```json
POST /recognize
{
  "photo": "data:image/jpeg;base64,/9j/4AAQSkZJRg...",
  "targets": ["T001", "T002"],
  "threshold": 0.4
}
```

返回的 `match` 为相似度降序列表，包含 `is_target` 标记。

## 数据存储

### persons 集合

- `_id`: ObjectId
- `name`: 人物姓名
- `number`: 唯一编号
- `photo_path`: 裁剪人脸图片路径
- `bbox`: 人脸框字符串 `x,y,w,h`
- `embedding`: 512 维特征向量（Binary, float32 bytes）
- `tip`: 图像质量提示

### 统计集合

- `api_call_logs`: 详细请求日志（TTL 清理）
- `api_stats_hourly`: 按小时聚合统计（TTL 清理）

## 说明与注意事项

- `photo` 字段必须是 `data:image/...;base64,` 前缀的 Base64 字符串。
- targets 为人员编号列表（`number`），匹配阈值为 `threshold / 2`。
- Dlib 检测使用进程池，`threading.max_workers` 建议 1~2。
- 日志由 `LOG_LEVEL` 与 `LOG_DIR` 环境变量控制，默认写入 `/app/logs/facerecapi.log`。
- Web 管理页 `/` 使用 HTTP Basic 鉴权，账号密码来自 `frontlogin` 配置。

## 联系方式

- 邮箱: seonzheung@gmail.com

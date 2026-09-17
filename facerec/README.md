# FaceRec 人脸识别算子

FaceRec 使用 InsightFace `buffalo_l` 检测并对齐整图中的多张人脸，使用 FastDeploy
ArcFace 生成 512 维 embedding，并从 MongoDB 人员库完成匹配。Dlib 68 点检测保留为显式
可选路径，不作为 InsightFace 失败后的自动回退。

## 运行合同

- Python 包与唯一入口：`app.main:app`
- 本地 Conda 环境：`facerecapi`（Python 3.10）
- 本地端口：`8003`
- 容器端口：`8000`
- 根配置：`config.toml`，可由 `CONFIG_PATH` 覆盖
- 模型目录：项目根 `ai_models/`
- 可变数据：项目根 `logs/` 与 `media/`
- 人员事实与 embedding：MongoDB

FaceRec 不连接平台 Redis。平台注册、心跳和租约均通过 Control Service 完成。算子只处理
请求中携带的图片，不拉取 RTSP、视频或课程数据。

## 目录结构

```text
facerec/
├── app/                  # FastAPI 应用包
├── ai_models/            # 七个运行模型，二进制不进入 Git
├── docker/               # 镜像、入口和独立 Compose
├── docs/                 # API 与运维补充文档
├── logs/                 # logs/{instance_id}/application.log
├── media/                # 可变业务媒体
├── scripts/              # 宿主机初始化脚本
├── tests/                # 单元、契约和真实推理 fixture
├── config.toml
├── config.example.toml
└── requirements.txt
```

## 模型

运行前必须在项目根准备以下文件，且文件非空：

```text
ai_models/
├── ms1mv3_arcface_r100.onnx
├── shape_predictor_68_face_landmarks.dat
└── models/buffalo_l/
    ├── det_10g.onnx
    ├── w600k_r50.onnx
    ├── 2d106det.onnx
    ├── 1k3d68.onnx
    └── genderage.onnx
```

模型二进制被 Git 忽略。完整说明见 [ai_models/README.md](ai_models/README.md)。无论检测器
选择 InsightFace 还是 Dlib，最终 embedding 都由 `ms1mv3_arcface_r100.onnx` 生成，以保持
现有 MongoDB 人员库的特征空间不变。

## 配置

从 `config.example.toml` 创建部署配置。关键字段如下：

```toml
[face_detection]
detector = "insightface" # 或 "dlib"

[face_detection.insightface]
model_name = "buffalo_l"
model_path = "ai_models"
det_size = 320
det_thresh = 0.75

[gpu]
device = "cpu" # 或 cuda:N

[runtime]
require_gpu = false

[platform]
registration_enabled = false
control_service_url = ""
heartbeat_interval_seconds = 5
max_concurrent_requests = 128

[image]
save_person_photo = false
```

`[gpu].device` 同时控制 InsightFace 与 ArcFace。生产 GPU 配置必须使用 `cuda:N` 并设置
`runtime.require_gpu=true`；任一后端缺少 CUDA provider、设备越界或初始化失败时，实例不会
宣告 ready，也不会静默回退 CPU。`threading.max_workers` 是隔离检测进程数，与平台声明容量
`max_concurrent_requests` 含义独立。

MongoDB 用户名和密码可分别由 `FACEREC_MONGO_USERNAME`、`FACEREC_MONGO_PASSWORD`
覆盖。启用平台注册时还需设置 `PLATFORM_INSTANCE_ID`、`PLATFORM_SERVICE_URL` 和
`PLATFORM_OPERATOR_REGISTRY_TOKEN`。

## 本地验证

```bash
conda run -n facerecapi python -m compileall -q app
conda run -n facerecapi python -c "from app.main import app; print(app.title)"
conda run -n facerecapi python -m pip check
conda run -n facerecapi python -m pytest -q tests
conda run -n facerecapi python -m uvicorn app.main:app \
  --host 127.0.0.1 --port 8003 --workers 1
```

启动后检查：

```bash
curl http://127.0.0.1:8003/ops/health
curl http://127.0.0.1:8003/ops/metadata
curl http://127.0.0.1:8003/ops/status
```

`/ops/health` 保持 HTTP 200，通过 `healthy` 或 `degraded` 表达 MongoDB、ArcFace 和检测
worker 状态。`/ops/metadata` 固定报告 `operator_code=facerec` 与
`capabilities=["recognize"]`；`/ops/status` 提供 readiness、生命周期、在途请求和声明容量。

真实本地推理使用 `tests/data/常泽宇.png`。默认 `save_person_photo=false` 时，录入仍会写入
非空 512 维 embedding，但 `photo_path` 为空且 `media/person_photos/` 不写原图。

## API 兼容边界

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/recognize` | 单图多人脸识别 |
| `POST` | `/recognize/batch` | 单人多帧聚合识别 |
| `POST` | `/persons` | 按 `number` 新增或更新人员 |
| `POST` | `/persons/batch` | 批量新增或更新人员 |
| `GET` | `/persons` | 分页读取人员 |
| `POST` | `/persons/search` | 查询人员 |
| `DELETE` | `/persons/delete` | 删除人员 |
| `GET` | `/ops/health` | 健康与模型状态 |
| `GET` | `/ops/metrics` | 系统和应用指标 |
| `GET` | `/ops/stats/*` | API 统计 |

`POST /recognize` 请求字段保持为 `photo`、`targets`、`threshold`，不接收 `points`/ROI。
响应继续使用 `status_code`、`has_face`、单个主脸 `bbox` 和按人员编号去重、相似度降序的
`match`，不得改用上游的驼峰字段或 `bboxs`。

## 日志与数据

日志同时写 stdout 和 `logs/{instance_id}/application.log`，格式为 JSON Lines，默认单文件
上限 100 MiB，归档保留七天。日志不得包含 Base64、媒体字节、完整请求/响应、凭据、人员
标识或 embedding。

MongoDB 是人员事实与 embedding 的唯一来源。升级不会自动删除、改写或批量重算已有
embedding；三个平台实例通过共享 MongoDB 观察人员新增、更新与删除，不存在算子侧 Redis
cache 失效流程。

## Docker 与平台部署

镜像和独立运行说明见 [docker/README.md](docker/README.md)。正式资产命名固定为：

- Git 目录：`facerec/`
- 镜像 repository：`algorithm-facerec`
- 实例：`facerec-gpu0`、`facerec-gpu1`、`facerec-gpu2`

生产平台适配与最终验收在 `192.168.29.11` 完成。构建必须复用并保留 BuildKit cache，
不得使用 `--no-cache` 或执行 builder/buildx/system prune。

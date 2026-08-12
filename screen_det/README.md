# 屏幕检测与画面质量检测服务

基于 **FastAPI + OpenCV + YOLO** 的 HTTP 检测服务：

- **倾斜检测** `/detect_tilt`：OpenCV CPU 线段角度
- **屏幕类型检测** `/detect_screen`：YOLO GPU（`model/screen.pt`）
- **画面异常检测** `/detect_quality_abnormal`：OpenCV CPU 规则，覆盖虚焦、偏色、雪花噪点、花屏
- **镜头遮挡检测** `/detect_occlusion`：YOLO-seg 单类分割，输出遮挡面积占比
- **全量聚合检测** `/detect_all`：单图一次返回倾斜、屏幕、画面异常、遮挡检测结果

| 项目 | 说明 |
|------|------|
| 服务名 | `tilt-detection-service` |
| 默认端口 | `8880`（直连 Uvicorn，无 Nginx） |
| OpenCV 推理 | **CPU**（倾斜、画面异常） |
| YOLO 推理 | 屏幕与遮挡模型统一由`[yolo].device`配置，启动时全部加载并预热 |
| 配置 | 根目录 `config.toml`（Docker 建议挂载） |
| 接口文档 | [docs/API接口文档.md](./docs/API接口文档.md) |

---

## 目录

- [API 接口文档](docs/API接口文档.md)
- [功能特性](#功能特性)
- [算法原理](#算法原理)
- [项目结构](#项目结构)
- [环境要求](#环境要求)
- [本地开发（Conda）](#本地开发conda)
- [API 说明](#api-说明)
- [配置说明](#配置说明)
- [Docker 部署](#docker-部署)
- [存储与资源风险](#存储与资源风险)
- [压测与性能](#压测与性能)
- [测试记录](#测试记录)
- [常见问题](#常见问题)

---

## 功能特性

- 单图 / 批量检测：JSON `{"images": "<base64>"}` 或数组
- **异步接口** `/detect_tilt`、`/detect_screen`、`/detect_quality_abnormal`、`/detect_occlusion`、`/detect_all`：CPU/GPU 推理在线程池执行
- 画面异常支持多异常同时命中，枚举：`1=虚焦`、`2=偏色`、`3=雪花噪点`、`4=花屏`
- 镜头遮挡限定为镜头前或镜头不远处遮挡，返回 `is_occluded`、`occlusion_area_ratio`、本次实际 `threshold` 与 `area_ratio`
- YOLO **启动预加载 + GPU warmup**，health 返回 `ready` / `warmed_up`
- 健康检查、运行时配置查询、**热重载** `config.toml`（OpenCV 阈值可热重载，GPU/worker 需重启）
- 请求访问日志、应用日志（**轮转**）、`X-Request-ID` 追踪
- Docker：`pytorch/cuda11.8` 基础镜像 + Cython 编译 + AES-GCM 模型保护

> 接口返回的 `start_time`、`end_time` 为 **北京时间** 对应的毫秒时间戳字符串。

---

## 算法原理

倾斜检测核心实现在 `app/services/tilt_detector.py`：

1. **解码**：Base64 → PIL RGB → OpenCV BGR；支持 `data:image/...;base64,` 前缀；限制最大体积（默认 10MB）
2. **预处理**：灰度 → 高斯模糊 → Canny 边缘
3. **线段检测**：`cv2.createLineSegmentDetector` 提取线段
4. **筛选**：保留长度 ≥ 图宽 × `min_line_length_ratio` 的线段；角度落在水平带（约 ±30°）或垂直带（约 60°–120°）
5. **聚合**：按角度排序后截取中间段（`trim_start_ratio`–`trim_end_ratio`），以线段长度为权重求平均，得到 **整体倾斜角** `angle`（绝对值，单位：度）
6. **判定**：`angle > tilt_threshold` 时内部记为倾斜（`is_tilted`）；API 响应主要返回 `angle`，阈值可通过配置调整

`model/classes.txt` 为相关业务类别标签（如 `askew-screen`、`normal-screen` 等），**当前倾斜检测 API 未加载该分类模型**，仅作业务参考。

画面异常核心实现在 `app/services/quality_abnormal_detector.py`：

1. Base64 解码并按配置缩放，生成 BGR、gray、HSV、Lab。
2. 按固定顺序检测：偏色 → 雪花噪点 → 虚焦 → 花屏。
3. 偏色使用 Lab/RGB/HSV 全局色彩偏移；雪花噪点使用高频噪声与边缘密度；虚焦使用 Laplacian 方差和边缘密度，并受雪花噪点分数修正。
4. 花屏第一版使用固定网格异常块、形态学连接、连通区域/面积占比判断；明显花屏样例目标约 70% 准确率。

镜头遮挡核心实现在 `app/services/occlusion_detector.py`：

1. 默认使用单类 YOLO-seg 权重 `model/occlusion.pt` 预测 `occlusion` mask。
2. 使用 `threshold` 过滤低置信度 mask，多 mask 取并集。
3. 计算 `occlusion_area_ratio = mask并集面积 / 整图面积`。
4. 使用 `area_ratio` 判定是否遮挡；默认 `threshold=0.25`、`area_ratio=0.2`。
5. 请求可选传入 `threshold` 和 `area_ratio` 覆盖本次检测默认值；响应会返回本次实际使用的阈值。

---

## 项目结构

```
screen_det/
├── app/
│   ├── main.py              # FastAPI 入口、中间件、异常处理
│   ├── api/v1/
│   │   ├── router.py        # 聚合各功能路由
│   │   ├── tilt.py          # 倾斜检测
│   │   ├── screen.py        # 屏幕类型 YOLO 检测
│   │   ├── quality_abnormal.py # 画面异常检测
│   │   ├── occlusion.py     # 镜头遮挡检测
│   │   ├── health.py        # 健康检查
│   │   ├── config.py        # 配置查询 / 热重载
│   │   └── common.py        # 公共工具（时间戳等）
│   ├── core/
│   │   ├── config.py        # 读取 config.toml
│   │   ├── logging.py       # 日志初始化
│   │   └── state.py         # 请求计数等运行时状态
│   ├── schemas/             # Pydantic 模型
│   └── services/
│       ├── tilt_detector.py
│       ├── screen_detector.py
│       ├── quality_abnormal_detector.py
│       ├── occlusion_detector.py
│       ├── image_preprocess.py
│       └── yolo_compat.py
├── config.toml
├── requirements.txt         # 本地 Conda
├── docker/                  # Docker构建、部署验收与模型保护
│   ├── Dockerfile
│   ├── requirements-docker.txt
│   ├── start.sh
│   └── models-encrypted/    # 本地生产材料，不提交Git
├── model/screen.pt          # 屏幕类型 YOLO 权重（打入镜像）
├── model/occlusion.pt            # 镜头遮挡 YOLO-seg 权重（部署时提供）
├── scripts/                 # 验收与本地调试
├── test/
│   ├── tilt_img/            # detect_tilt 测试图
│   ├── ok_img/              # detect_screen 正常样例
│   ├── error_img/           # detect_screen 异常样例
│   └── 图像检测/             # 画面异常 / 遮挡 / 歪斜样例
└── logs/                    # 运行日志（建议 Docker 挂载）
```

所有接口均使用无前缀路径：

| 用途 | 路径示例 |
|------|----------|
| 健康检查 | `/health` |
| 倾斜检测 | `/detect_tilt` |
| 屏幕检测 | `/detect_screen` |
| 画面异常检测 | `/detect_quality_abnormal` |
| 镜头遮挡检测 | `/detect_occlusion` |
| 全量聚合检测 | `/detect_all` |

---

## 环境要求

- **Python**：3.10+（本地 Conda `screen_det`）；Docker 为 Python 3.11（PyTorch 镜像）
- **GPU**：`/detect_screen` 需要 NVIDIA 驱动 + `--gpus`；`/detect_tilt` 仅 CPU
- **Docker**：NVIDIA Container Toolkit；基础镜像 `pytorch/pytorch:2.6.0-cuda11.8-cudnn9-runtime`

---

## 本地开发（Conda）

### 1. 激活环境并安装依赖

```bash
cd /root/workspace/screen_det
conda create -n screen_det python=3.11
conda activate screen_det
pip install -r requirements.txt
```

### 2. 调整 GPU（可选）

编辑`config.toml`中的`[yolo].device`；可选`cpu`、`mps`或`cuda:N`，单GPU建议`[server].workers = 1`。

### 3. 启动服务

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8880 --workers 1
```

### 4. 健康检查

```bash
curl http://127.0.0.1:8880/
curl http://127.0.0.1:8880/health
```

### 5. 使用测试图片

测试数据在 `test/` 目录：

| 目录 | 接口 |
|------|------|
| `test/tilt_img/` | `/detect_tilt` |
| `test/ok_img/`、`test/error_img/` | `/detect_screen` |
| `test/图像检测/画面异常/` | `/detect_quality_abnormal` |
| `test/图像检测/遮挡/` | `/detect_occlusion` |

验收脚本（自动拉起服务、跑用例、关闭）：

```bash
bash docker/run_deploy_verify.sh
```

服务层单项验收：

```bash
python scripts/validate_quality_abnormal_samples.py
python scripts/validate_occlusion_samples.py
python scripts/evaluate_yolo_occlusion.py --images /path/to/images --output-dir test/reports/yolo_eval
```

---

## API 说明

### 根路径 `GET /`

返回服务名、版本及各接口路径。

### 健康检查 `GET /health`

```json
{
  "status": "success",
  "ready": true,
  "elapsed_time": "0h 5m 12s",
  "total_requests": 42,
  "memory_mb": 1451.0,
  "yolo": {
    "device": "cuda:0",
    "yolo_device_resolved": "cuda:0",
    "tilt_inference_device": "cpu"
  },
  "screen_model": {
    "loaded": true,
    "warmed_up": true,
    "device": "cuda:0",
    "gpu_memory_mb": 512.0
  },
  "occlusion_model": {
    "loaded": true,
    "warmed_up": true,
    "device": "cuda:0"
  }
}
```

未就绪时 `ready: false`，HTTP 503。

### 倾斜检测 `POST /detect_tilt`

| 请求方式 | Content-Type | Body |
|----------|--------------|------|
| 纯 Base64 | `text/plain` | 图片 Base64 字符串（可无 data URL 前缀） |
| JSON | `application/json` | `{"images": "<base64>"}`；可选 `"tilt_threshold": 1.5` 覆盖配置 |

`tilt_threshold` 默认读 `config.toml` 的 `[detection].tilt_threshold`；`text/plain` 请求无法传该参数，使用默认值。

**成功响应**

```json
{
  "code": 200,
  "start_time": "1753791280207",
  "end_time": "1753791280225",
  "msg": "检测完成",
  "tilt_threshold": 1.5,
  "result": {
    "is_tilted": true,
    "angle": 2.35,
    "cost_ms": 37.59
  }
}
```

`is_tilted`：`angle > tilt_threshold` 时为 `true`。

### 屏幕类型检测 `POST /detect_screen`（YOLO）

使用`model/screen.pt`，仅返回**label 0–3**（蓝/黑/白/正常屏）。推理设备由`config.toml`的`[yolo].device`统一指定。

**请求**（`Content-Type: application/json`）

单图：

```json
{
  "images": "base64字符串",
  "conf": 0.25,
  "iou": 0.45
}
```

多图：`images` 改为字符串数组；`conf` / `iou` 可选，默认读 `[screen_detection]`。

**成功响应**

```json
{
  "code": 200,
  "start_time": "1753791280207",
  "end_time": "1753791280274",
  "msg": "检测完成",
  "conf": 0.25,
  "iou": 0.45,
  "total": 1,
  "results": [
    {
      "index": 0,
      "cost_ms": 48.6,
      "primary": {
        "label": 3,
        "confidence": 0.926,
        "box": [936, 55, 1697, 493]
      },
      "detections": [
        {
          "label": 3,
          "confidence": 0.926,
          "box": [936, 55, 1697, 493]
        }
      ]
    }
  ]
}
```

| label | 含义 |
|-------|------|
| 0 | blue-screen |
| 1 | black-screen |
| 2 | white-screen |
| 3 | normal-screen |

`box` 为 `[x1, y1, x2, y2]`：左上角 + 右下角，像素坐标。

### 画面异常检测 `POST /detect_quality_abnormal`

**请求**（`Content-Type: application/json`）：

```json
{
  "image": "base64字符串"
}
```

**响应示例**：

```json
{
  "code": 200,
  "msg": "检测完成",
  "is_abnormal": true,
  "abnormal_types": [1, 4],
  "results": [
    { "type": 1, "score": 0.76, "message": "疑似虚焦" },
    { "type": 4, "score": 0.71, "message": "疑似花屏" }
  ],
  "message": "检测到画面异常：虚焦、花屏"
}
```

`abnormal_types` 中出现的类型才会出现在 `results` 中。

| type | 含义 |
|------|------|
| 1 | 虚焦 |
| 2 | 偏色 |
| 3 | 雪花噪点 |
| 4 | 花屏 |

### 镜头遮挡检测 `POST /detect_occlusion`

**请求**（`Content-Type: application/json`）：

```json
{
  "image": "base64字符串"
}
```

**响应示例**：

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

请求可选传入 `threshold` 和 `area_ratio` 覆盖本次检测阈值；响应中的 `threshold` 与 `area_ratio` 表示实际使用值。遮挡定义限定为镜头前或镜头不远处遮挡；默认 YOLO-seg 后端的 `occlusion_area_ratio` 是有效 mask 并集面积占整图比例。

### 全量聚合检测 `POST /detect_all`

单图一次执行倾斜、屏幕、画面异常和遮挡检测。除 `image` 外，其他字段都可不传，默认读取 `[aggregate_detection]`；请求传入时只覆盖本次调用。

```json
{
  "image": "base64字符串",
  "tilt_threshold": 1.5,
  "screen_conf": 0.25,
  "screen_iou": 0.45,
  "occlusion_threshold": 0.25,
  "occlusion_area_ratio": 0.2,
  "include": ["tilt", "screen", "quality_abnormal", "occlusion"]
}
```

响应顶层 `problem_types` 是模块级业务问题数组，枚举值为 `tilt`、`screen`、`quality_abnormal`、`occlusion`。例如 `["tilt"]` 表示只有倾斜模块检测出问题，其他已成功执行且未出现在数组中的模块为无业务异常。

```json
{
  "code": 200,
  "msg": "检测完成",
  "executed_modules": ["tilt", "screen", "quality_abnormal", "occlusion"],
  "failed_modules": [],
  "effective_params": {
    "tilt_threshold": 1.5,
    "screen_conf": 0.25,
    "screen_iou": 0.45,
    "occlusion_threshold": 0.25,
    "occlusion_area_ratio": 0.2,
    "include": ["tilt", "screen", "quality_abnormal", "occlusion"],
    "device": "cpu"
  },
  "problem_types": [],
  "tilt": { "code": 200, "result": { "is_tilted": false, "angle": 0.8, "cost_ms": 20.3 } },
  "screen": { "code": 200, "primary": { "label": 3, "confidence": 0.91, "box": [100, 50, 900, 500] }, "detections": [] },
  "quality_abnormal": { "code": 200, "is_abnormal": false, "abnormal_types": [], "results": [] },
  "occlusion": { "code": 200, "is_occluded": false, "occlusion_area_ratio": 0.0 }
}
```

### 配置 `GET /config`

返回当前 `app`、`server`、`gpu`、`detection`、`screen_detection`、`quality_abnormal_detection`、`occlusion_detection`、`aggregate_detection`、`runtime` 配置快照。

### 重载配置 `POST /config/reload`

重新读取 `config.toml` 中的检测阈值、YOLO 遮挡配置和部分屏幕检测配置，无需重启；遮挡 YOLO 模型缓存会同步清理。

```bash
curl -X POST http://127.0.0.1:8880/config/reload
```

### 错误响应

- `400`：`{"code": 400, "msg": "..."}`（参数/Base64/图片无效等）
- `500`：`{"code": 500, "msg": "..."}`

---

## 配置说明

所有配置默认读取项目根目录 `config.toml`。可通过环境变量 `CONFIG_PATH` 覆盖；
绝对路径直接使用，相对路径始终以项目根目录为基准解析，与当前工作目录无关。

### 应用与服务

```toml
[app]
name = "tilt-detection-service"
version = "1.0.0"
debug = false

[server]
host = "0.0.0.0"
port = 8880
workers = 1
```

### YOLO设备与模型保护

```toml
[yolo]
device = "cpu"

[model_protection]
enabled = false
encrypted_model_root = "/run/screen-det/models-encrypted"
key_file = "/run/screen-det/models-encrypted/model.key"
decrypted_temp_root = "/dev/shm/screen-det-models"
cleanup_after_load = true

[screen_detection]
weights_path = "model/screen.pt"
conf = 0.25
iou = 0.45
allowed_class_ids = [0, 1, 2, 3]
max_batch_size = 16
```

| 模块 | 设备 |
|------|------|
| 倾斜检测 `/detect_tilt` | **CPU**（OpenCV） |
| 屏幕检测 `/detect_screen` | `[yolo].device` |
| 镜头遮挡 `/detect_occlusion` | `[yolo].device` |

### 检测参数

| 键 | 默认值 | 含义 |
|----|--------|------|
| `tilt_threshold` | `1.5` | 判定倾斜的角度阈值（度） |
| `min_line_length_ratio` | `0.1` | 最小线段长度 = 图宽 × 该比例 |
| `min_valid_lines` | `5` | 至少多少条有效线段才计算角度 |
| `trim_start_ratio` / `trim_end_ratio` | `0.2` / `0.8` | 角度样本截断区间 |
| `gaussian_kernel_size` | `5` | 高斯核（自动调整为奇数 ≥3） |
| `canny_threshold1` / `canny_threshold2` | `50` / `150` | Canny 双阈值 |
| `horizontal_angle_min/max` | `-30` / `30` | 水平参考线角度范围 |
| `vertical_angle_min/max` | `60` / `120` | 垂直参考线角度范围 |

```toml
[detection]
tilt_threshold = 1.5
# ... 其余见 config.toml
```

### 画面异常与遮挡参数

```toml
[quality_abnormal_detection]
enabled = true
analyze_max_side = 960
color_cast_lab_threshold = 18.0
snow_noise_threshold = 14.0
blur_laplacian_threshold = 450.0
glitch_min_area_ratio = 0.18
glitch_grid_rows = 16
glitch_grid_cols = 24

[occlusion_detection]
enabled = true
analyze_max_side = 960
threshold = 0.25
area_ratio = 0.2
yolo_seg_weights_path = "model/occlusion.pt"
yolo_imgsz = 960
yolo_retina_masks = true

[aggregate_detection]
enabled = true
default_modules = ["tilt", "screen", "quality_abnormal", "occlusion"]
tilt_threshold = 1.5
screen_conf = 0.25
screen_iou = 0.45
occlusion_threshold = 0.25
occlusion_area_ratio = 0.2
```

遮挡检测只使用 YOLO-seg；`threshold` 是 YOLO 置信度阈值，`area_ratio` 是最终面积判定阈值。数据集建议：单类 `occlusion` 分割标注；可行性实验约 80–150 张遮挡正样本 + 200–500 张正常负样本，第一版可用建议 300–500 张遮挡正样本 + 500–1000 张正常负样本，生产稳定建议 1000+ 正样本 + 2000+ 正常负样本。

聚合接口`/detect_all`使用`[aggregate_detection]`作为阈值来源；所有YOLO推理统一使用`[yolo].device`，不接受请求覆盖。

### 日志与运行时

```toml
[logging]
level = "INFO"
log_dir = "logs"
access_log = "access.log"
app_log = "app.log"

[runtime]
max_image_bytes = 10485760   # 10MB
```

---

## Docker 部署

### 构建

```bash
docker build -f docker/Dockerfile \
  -t screen_det:v1.0_260525 .
```

- 基础镜像：`pytorch/pytorch:2.6.0-cuda11.8-cudnn9-runtime`（含 torch+cu118）
- 依赖：`docker/requirements-docker.txt`（不含 torch，避免覆盖基础镜像）
- 业务代码：构建阶段编译为Cython扩展，运行层不保留核心Python源码
- 模型：生产只读挂载`docker/models-encrypted/`，镜像不包含任何模型文件
- 模型：`model/screen.pt` 打入镜像；`config.toml` **运行时挂载**

### 运行

```bash
docker run -d \
  --name tilt-api \
  --restart unless-stopped \
  --gpus all \
  -p 8880:8880 \
  -v /path/to/config.toml:/app/config.toml:ro \
  -v /path/to/models-encrypted:/run/screen-det/models-encrypted:ro \
  -v /path/to/logs:/app/logs \
  screen_det:v1.0_260525
```

### 验证

```bash
docker logs tilt-api | grep -E "preload|warmup"
curl -s http://127.0.0.1:8880/health
nvidia-smi
```

| 变更类型 | 操作 |
|----------|------|
| 仅 `config.toml` | `docker restart tilt-api` |
| 代码 / `docker/Dockerfile` / `docker/start.sh` | 重新 `docker build` + `docker run` |

> 完整的加密模型生成、双挂载、启动后清理和重启限制见`docker/README.md`。

---

## 存储与资源风险

本项目 **无 Redis/磁盘缓存**，不存在典型缓存击穿；需关注以下资源边界：

| 类型 | 机制 | 上限 / 说明 |
|------|------|-------------|
| **日志磁盘** | `RotatingFileHandler` | 每文件 10MB × 11 份 × 2 个日志 ≈ 220MB |
| **单图内存** | `max_image_bytes` | 默认 10MB / 张 |
| **批量** | `max_batch_size` | 默认 16 张 / 请求 |
| **YOLO 缓存** | `YOLO_CONFIG_DIR` → `logs/.ultralytics` | 避免写 `/tmp` |
| **并发内存** | 无全局限流 | 高并发大图为主要风险，建议网关限流 |

单 GPU 生产建议：`workers = 1`，避免多 worker 重复占用显存。

---

## 压测与性能

压测前确认服务正常：

```bash
curl http://127.0.0.1:8880/health
```

**完整企业级套件**（预热、异步/同步阶梯、四图单测、批量混合、长稳）：

```bash
bash scripts/run_enterprise_benchmark.sh
# 指定远程地址
BASE_URL=http://10.80.5.197:8880 bash scripts/run_enterprise_benchmark.sh
```

结果保存在 `benchmark_reports/<时间戳>/`。关注输出中的 `failed`、`qps`、`latency_ms.p95`、`latency_ms.p99`。

压测期间可观察：

```bash
docker logs -f tilt-api    # Docker 部署时
watch -n 1 nvidia-smi
```

---

## 测试记录

在 **Conda 环境 `screen_det`**、项目根目录 **`text*.jpg`** 上于 **2026-05-20** 执行验证（默认 `tilt_threshold = 1.5`）。

### 算法直连（不经过 HTTP）

| 图片 | angle (°) | 是否超过阈值 | 说明 |
|------|-----------|--------------|------|
| text0.jpg | 2.35 | 是 | 明显倾斜样例 |
| text1.jpg | 0.52 | 否 | |
| text2.jpg | 0.06 | 否 | |
| text3.jpg | 0.84 | 否 | |

### HTTP 异步接口 `POST /detect_tilt`

| 图片 | angle | cost_ms（约） |
|------|-------|----------------|
| text0.jpg | 2.35 | 38 |
| text1.jpg | 0.52 | 139 |
| text2.jpg | 0.06 | 136 |
| text3.jpg | 0.84 | 137 |

JSON 请求 `{"images": "<base64>"}`：`result.angle` / `result.cost_ms` 与上表一致。

### 轻量压测（text0.jpg，20 请求，并发 5）

| 指标 | 值 |
|------|-----|
| success / failed | 20 / 0 |
| QPS | ~94.3 |
| 延迟 avg / p95 | ~52ms / ~55ms |

> 首次请求或冷启动可能略慢；生产评估建议先预热再跑 `run_enterprise_benchmark.sh`。

---

## 常见问题

**Q：配置`device="cuda:0"`但CUDA不可用会怎样？**
A：两个YOLO模型启动加载直接失败，服务不会静默回退CPU，也不会进入ready。

**Q：nginx 目录去哪了？**
A：当前架构为 Uvicorn 直连 `-p 8880:8880`，不再需要 Nginx 反向代理。

**Q：返回 `有效参考线段不足` 或 `未检测到有效直线`？**
A：画面缺少清晰水平/垂直边缘，可调低 `canny_threshold*`、`min_line_length_ratio` 或 `min_valid_lines`（需结合误检率评估）。

**Q：如何修改倾斜判定阈值？**
A：修改 `config.toml` 中 `[detection].tilt_threshold`，然后 `POST /config/reload`。

**Q：Postman 如何测单图？**
A：`POST http://<host>:8880/detect_tilt`，Body 选 raw，类型 text，直接粘贴 Base64（不要加 JSON 引号）。

---

## 依赖版本

见 `requirements.txt` / `docker/requirements-docker.txt`：

- fastapi、uvicorn、opencv-python-headless、ultralytics
- Docker 镜像内 torch 由 `pytorch/pytorch:2.6.0-cuda11.8-cudnn9-runtime` 提供

---

## 许可证与联系

企业内部算法服务项目；部署地址与网络策略以实际环境为准。历史文档中的路径 `detect_tilt` 与对外 IP 仅作示例，请以当前仓库与运维配置为准。

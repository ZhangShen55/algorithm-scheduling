# VBas 视觉行为分析算子

VBas 是可独立部署的 FastAPI 图片推理服务，负责学生人数/行为和教师站、坐、板书、讲授等单帧检测。服务不负责课程视频下载、抽帧、Kafka 消费、行为时间线聚合或入库。

课程级视觉编排已抽离到：

- 本地：`/Users/zhangshen/Documents/workspace/jy-vision-orchestrator-server`
- Git：`git@github.com:ZhangShen55/jy-vision-orchestrator-server.git`

## 项目结构

```text
vbas/
├── app/                       # Python 包
│   ├── main.py                   # FastAPI app
│   ├── api/                       # HTTP 路由
│   ├── core/                      # 配置、模型生命周期、加密模型
│   ├── schemas/                   # 请求/响应模型
│   ├── services/                  # 学生/教师推理
│   └── vendor/DirectMHP/          # 教师头部姿态依赖
├── models/                    # 明文模型
├── models-encrypted/          # 加密模型
├── docker/                    # 镜像和 Compose
├── scripts/                   # 构建/模型保护工具
├── tests/                     # 回归测试与图片样本
├── config.toml                # 本地配置
└── config.toml.example        # 配置样例
```

## 配置与模型

默认读取项目根目录的 `config.toml`，可用 `CONFIG_PATH` 指定其他文件。本地 CPU 验证使用：

```toml
GPU_ID = "cpu"
```

### 平台注册与运行配置

本地根配置默认不注册且允许 CPU；受控 GPU 部署使用
`algorithm-scheduling-platform/deploy/config/operators/vbas.gpu.toml`：

| 字段 | 本地根配置 | 受控部署 | 说明 |
| --- | --- | --- | --- |
| `platform.registration_enabled` | `false` | `true` | 是否主动注册到调度平台 |
| `platform.control_service_url` | `""` | `http://control-service:18100` | 注册与心跳地址 |
| `platform.heartbeat_interval_seconds` | `5` | `5` | 心跳间隔 |
| `platform.max_concurrent_requests` | `128` | `128` | 学生、教师及可选头部姿态共享容量 |
| `runtime.require_gpu` | `false` | `true` | `true` 时算子设备必须是可用 CUDA |

Compose 继续管理实例 ID、服务 URL、注册 Token、物理 GPU/可见设备、`CONFIG_PATH`、端口和
`UVICORN_WORKERS=1`。`TIAS.MaxConcurrentBatches`、`TIAS.MaxQueueSize`、模型锁和批次大小仍负责
本地模型安全，不会被平台注册容量 `128` 覆盖；教师请求中的可选头部姿态也不形成独立容量池。

必要模型放在 `models/`：

- `person_count.pt`
- `face_count.pt`
- `student.pt`
- `teacher_behavior.pt`
- `cmu_m_1280_e200_t40_lw010_best.pt` 和 `cmu_panoptic_coco.yaml`（启用 DirectMHP 时）

`[TIAS]`、`AiQualityBaseUrl`、`TIAS_*` 环境变量及 `tias_model_key` 是现有注册/模型保护兼容名称，本次结构整改不修改这些协议字段。

## 启动

```bash
conda activate jy-tias
pip install -r requirements_mac.txt   # macOS CPU
export CONFIG_PATH="$PWD/config.toml"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8981
```

容器与多实例运行见 [RUNNING.md](RUNNING.md) 和 [docker/README.md](docker/README.md)。

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/ImageDetect/student/v1.0.0` | 学生人数与行为检测 |
| `POST` | `/ImageDetect/teacher/v1.0.0` | 教师行为检测，可选头部姿态 |
| `GET` | `/AE/Health` | 模型就绪与健康状态 |
| `GET` | `/AE/WorkerStatus` | 实例并发/排队状态 |
| `PUT` | `/AE/Drain` | 实例进入排空状态 |

`StoragePath` 保持现有能力：可传 Base64/Data URL、HTTP(S) URL、绝对文件路径，或相对 `IMAGE_ROOT` 的路径。

单图请求示例：

```json
{
  "ImageList": [
    {
      "StoragePath": "/absolute/path/to/image.jpg",
      "ImageId": "frame-0001"
    }
  ],
  "task_id": "course-001",
  "batch_id": "student-001",
  "stream_type": "S"
}
```

## 验证

```bash
conda run -n jy-tias python -m compileall -q app scripts tests
conda run -n jy-tias python -m pytest -q tests
conda run -n jy-tias python -m pip check
```

最终验收需启动 `app.main:app`，直接调用学生和教师推理接口，不依赖外部视觉编排服务。

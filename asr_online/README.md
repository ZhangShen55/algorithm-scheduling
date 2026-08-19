# SeaCraftASR-Online

基于 [FunASR](https://github.com/modelscope/FunASR) 与 [ModelScope](https://github.com/modelscope/modelscope) 的**中文实时流式语音识别**服务。客户端通过 WebSocket 持续发送 16 kHz PCM 音频块，服务端返回带标点、带时间戳的增量识别结果。

## 功能概览

| 能力 | 说明 |
|------|------|
| 流式 ASR | Paraformer 在线模型，低延迟增量输出 |
| 实时标点 | ModelScope 标点模型，按静音/长度触发 |
| 实例路由 | 每个容器只运行一个 Uvicorn 端点，由算法调度平台按 WebSocket 会话选实例 |
| 状态接口 | 运行时长、会话统计、任务列表查询与清理 |

## 项目结构

```
asr_online/
├── app/main.py          # FastAPI 应用入口
├── config.toml             # 服务与模型配置（容器内挂载为 /config.toml）
├── docker/
│   ├── Dockerfile          # CUDA 12.1 + Conda 普通镜像构建
│   ├── Dockerfile.cython   # Cython 编译镜像构建
│   └── start.sh            # 容器启动脚本：单 Uvicorn、workers=1
├── requirements.txt        # Python 依赖
├── api/routes/
│   ├── ws_online.py        # WebSocket 在线识别
│   └── status.py           # /get_status、/clear_tasks_list
├── core/
│   ├── config.py           # 读取 TOML 配置
│   ├── models.py           # FunASR / ModelScope 模型加载
│   └── logging.py
├── utils/
│   ├── asr_stats.py        # 统计持久化（asr_stats.json）
│   └── character_utils.py  # 标点与文本拼接
└── test/
    └── test_api_ws_client.py  # WebSocket 客户端示例
```

## 架构说明

```
在线网关
   │ 建连时获取 asr_online 容量租约
   ▼
Uvicorn :8084  (app.main:app, workers=1)
   ├── WebSocket /v1.0.1/seacraft_asr_online
   └── HTTP /get_status 等
```

生产与本地均直接启动 `app.main:app`，默认端口 **8084**。多 GPU 或多实例时启动多个容器，每个容器使用独立端口、`PLATFORM_INSTANCE_ID`、`PLATFORM_SERVICE_URL` 和 GPU 标签注册。进程重启交给 Docker restart policy。

## 配置说明

配置文件路径由环境变量 `CONFIG_PATH` 指定；未设置时读取项目根 `config.toml`，相对路径也按项目根解析，Docker 内固定为 `/config.toml`。

### 平台注册与运行配置

项目根 `config.toml` 用于本地安全运行；受控三卡部署使用
`algorithm-scheduling-platform/deploy/config/operators/asr_online.gpu.toml`：

| 字段 | 本地根配置 | 受控部署 | 说明 |
| --- | --- | --- | --- |
| `platform.registration_enabled` | `false` | `true` | 是否主动注册到调度平台 |
| `platform.control_service_url` | `""` | `http://control-service:18100` | 注册与心跳地址 |
| `platform.heartbeat_interval_seconds` | `5` | `5` | 心跳间隔，必须为有限正数 |
| `platform.max_concurrent_requests` | `10` | `10` | 单实例可分发的 WebSocket 会话数 |
| `runtime.require_gpu` | `false` | `true` | `true` 时 CUDA 配置或设备不可用会启动失败 |

实例级和启动前事实仍由 Compose 管理，包括 `PLATFORM_INSTANCE_ID`、
`PLATFORM_SERVICE_URL`、`PLATFORM_OPERATOR_REGISTRY_TOKEN`、`PLATFORM_GPU_ID`、
`NVIDIA_VISIBLE_DEVICES`、`CONFIG_PATH`、端口和 `UVICORN_WORKERS=1`。这些字段不写入共享
TOML。平台容量按一个 WebSocket 会话计量，不改变在线模型原有的流式处理和会话生命周期。

```toml
id_engine = "online-1"
version = "seacraft-asr-online-v1.0.0"
device = "cpu"             # GPU 部署改为 cuda:0
ngpu = 0                    # CUDA 模式必须为 1
log_path = "./asr_online_service.log"

# 模型默认使用镜像内 ./model，可按需添加 [model_paths] 覆盖。
```

镜像默认使用内置 `./model` 目录；如需使用外部模型，可在 `config.toml` 中添加 `[model_paths]` 覆盖。
配置为 `cuda:<index>` 时不会自动回退 CPU；CUDA 不可用、索引越界或 `ngpu != 1` 都会失败关闭。

## API

### WebSocket 实时识别

- **路径**：`/v1.0.1/seacraft_asr_online`
- **协议**：`ws://<host>:8084/v1.0.1/seacraft_asr_online`
- **上行**：二进制帧，**16 kHz、单声道、int16 PCM**（每块建议约 960×N 采样点，与客户端发送节奏一致）
- **下行**：JSON，例如：

```json
{
  "key": "rand_key_xxxxxxxxxxxx",
  "text": "识别文本",
  "finished": false,
  "bg": 0.0,
  "ed": 0.48
}
```

识别逻辑要点：连续静音达到阈值或文本过长时触发标点并可能返回 `finished: true`；连接断开后更新在线会话统计。

### HTTP 状态

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/get_status` | 引擎 ID、版本、运行时长、在线完成数等 |
| DELETE | `/clear_tasks_list` | 清空 processing 任务列表 |

## 本地开发（无 Docker）

**环境要求**：Python 3.10、CUDA（可选）、已下载的 ASR/标点模型。

```bash
# 创建虚拟环境并安装依赖
python3.10 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 按需修改 config.toml 中的 model_paths 与 device
export CONFIG_PATH=./config.toml
python main.py
# 服务监听 http://0.0.0.0:8084
```

WebSocket 测试可参考 `test/test_api_ws_client.py`（需自备 16 kHz PCM/WAV 测试音频）。

## Docker 构建与运行

镜像基于 `nvcr.io/nvidia/cuda:12.1.1-cudnn8-runtime-centos7`，内含 Conda 环境 `seacraftasr_online`。**需要 NVIDIA GPU 与 nvidia-container-toolkit** 才能在容器内使用 CUDA。

### 构建镜像

在项目根目录执行，构建上下文仍是项目根目录：

```bash
cd /root/workspace/asr_online

# 基础构建
docker build -f docker/Dockerfile -t seacraft-asr-online:latest .

# 指定平台（如在 ARM/Mac 上为 x86 服务器构建）
docker build --platform linux/amd64 -f docker/Dockerfile -t seacraft-asr-online:latest .
```

首次构建会下载 CUDA 基础镜像、Miniconda 并通过 pip 安装 `requirements.txt`（含 PyTorch、FunASR 等），耗时较长，属正常现象。

### Cython 编译镜像（`docker/Dockerfile.cython`）

在标准镜像基础上，将 `app/main.py`、`api/`、`core/`、`utils/` 下的业务 `.py` 编译为 `.so`，用于减小源码暴露、略提升导入性能。

```bash
# 构建（需 Docker 构建环境能访问 gcc，镜像内已安装 Development Tools）
docker build -f docker/Dockerfile.cython -t seacraft-asr-online:cython .

# 运行方式与普通镜像相同
docker run -d --name seacraft-asr-online --gpus all -p 8084:8084 \
  -v /path/on/host/config.toml:/config.toml:ro \
  seacraft-asr-online:cython
```

本地仅做 Cython 编译（不打包镜像）：

```bash
pip install cython setuptools
python setup_cython.py build_ext --inplace
```

编译范围由 `setup_cython.py` 控制；`scripts/`、`test/`、`tests/` 不参与编译。

### 运行容器

模型目录需**挂载到容器内**与 `config.toml` 一致的路径（或挂载自定义 `config.toml`）：

```bash
docker run -d \
  --name seacraft-asr-online \
  --gpus all \
  -p 8084:8084 \
  -v /path/on/host/model_zoo:/var/model_zoo/model_asr:ro \
  -v /path/on/host/config.toml:/config.toml:ro \
  seacraft-asr-online:latest
```

仅使用镜像内默认配置时，可省略 config 挂载（镜像已将 `config.toml` 复制为 `/config.toml`），但**模型卷仍必须提供**：

```bash
docker run -d \
  --name seacraft-asr-online \
  --gpus all \
  -p 8084:8084 \
  -v /path/on/host/model_zoo:/var/model_zoo/model_asr:ro \
  seacraft-asr-online:latest
```

### 验证

```bash
# 健康与状态
curl http://localhost:8084/get_status

# 查看单个 Uvicorn 实例日志
docker logs -f seacraft-asr-online
```

WebSocket 压测或单路测试：将 `test/test_api_ws_client.py` 中的 `ws_url` 改为 `ws://localhost:8084/v1.0.1/seacraft_asr_online` 后运行。

### 常用 Docker 说明

| 项 | 值 |
|----|-----|
| 对外端口 | **8084**（Uvicorn） |
| 配置文件 | 容器内 `/config.toml`，可用 `-v` 覆盖 |
| 实例数 | 一容器一实例；通过多个容器和独立注册信息扩容 |
| GPU | `device` 建议与 `--gpus` 映射一致；多卡时可改 `cuda:1` 等 |

## 依赖摘要

- **Web**：FastAPI、uvicorn、websockets  
- **ASR**：funasr、modelscope、torch  
- **配置**：tomli  

完整版本见 `requirements.txt`。

## 注意事项

1. **显存**：在线 Paraformer + 标点模型会占用 GPU 显存；与离线主服务同机部署时建议使用不同 GPU（见 `config.toml` 注释）。  
2. **模型路径**：路径错误会导致启动时模型加载失败。  
3. **日志与统计**：运行时会生成 `asr_online_service.log`、`asr_stats.json`（已在 `.gitignore` 中忽略）。  
4. **构建网络**：Dockerfile 使用阿里云 yum/pip 镜像；若构建失败可检查网络或代理。

## 许可证

请根据组织/仓库实际许可证补充；本仓库未单独声明 LICENSE 文件时以上游 FunASR、ModelScope 及所用模型许可为准。

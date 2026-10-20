# SeaCraftASR-Online

基于 [FunASR](https://github.com/modelscope/FunASR) 与 [ModelScope](https://github.com/modelscope/modelscope) 的**中文实时流式语音识别**服务。客户端通过 WebSocket 持续发送 16 kHz PCM 音频块，服务端返回带标点、带时间戳的增量识别结果。

## 功能概览

| 能力 | 说明 |
|------|------|
| 流式 ASR | Paraformer 在线模型，低延迟增量输出 |
| 实时标点 | ModelScope 标点模型，按静音/长度触发 |
| 累计纠正 | v1.0.4 使用普通非流式 Paraformer 周期识别当前段累计音频，并单独补标点 |
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
│   ├── README.md           # 镜像构建和 docker run 部署命令
│   └── start.sh            # 容器启动脚本：单 Uvicorn、workers=1
├── requirements.txt        # Python 依赖
├── api/routes/
│   ├── ws_online.py        # WebSocket 在线识别
│   └── status.py           # /get_status、/clear_tasks_list
├── core/
│   ├── config.py           # 读取 TOML 配置
│   ├── correction_scheduler.py # v1.0.3 有界 SenseVoice 纠正 worker 池
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
   ├── WebSocket /v1.0.2/seacraft_asr_online
   ├── WebSocket /v1.0.3/seacraft_asr_online
   ├── WebSocket /v1.0.4/seacraft_asr_online
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

### WebSocket v1.0.2（实验接口）

- **路径**：`/v1.0.2/seacraft_asr_online`
- **直连地址**：`ws://<host>:8084/v1.0.2/seacraft_asr_online`
- **上行和基础响应字段**：与 v1.0.1 相同。
- **当前范围**：只在 `asr_online` 算子内开发和验证，`online_gateway_service` 暂不转发此路径。
- **结果语义**：同一语音段使用稳定 `key`；`finished=false` 是临时文本，`finished=true` 是可以覆盖临时文本的最终文本。

v1.0.2 的 FSMN-VAD 和句末二遍识别模型放在项目根目录的 `model/` 下，并通过
`config.toml` 的 `[model_paths]` 配置：

```toml
[model_paths]
vad_model_dir = "model/speech_fsmn_vad_zh-cn-16k-common-pytorch"
asr_online_correction_model_dir = "model/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
```

这两个模型不再读取 `ASR_ONLINE_VAD_MODEL_DIR` 和
`ASR_ONLINE_CORRECTION_MODEL_DIR` 环境变量。

未配置纠正模型时，v1.0.2 会在句末使用 fresh cache 对完整语音段再次调用在线模型，作为可运行的回放兜底。

v1.0.2 和 v1.0.3 在同一 WebSocket 会话中连续复用 VAD cache，并将 VAD 的毫秒级
起止时间换算为会话绝对样本位置。自然断点后的 PCM 继续保留到 pending buffer；
确认下一句起点后，纠正输入向前保留 150 ms pre-roll，但响应 `bg` 仍使用真实 VAD
起点。单段达到 42 个块（约 20.16 秒）时会强制完成，空闲 pending PCM 最多保留
6 秒。`vad_max_end_silence_ms` 默认是 1000，可在项目根 `config.toml` 调整；每个自然
断点先进入候选状态；候选结束后若在 `vad_merge_max_gap_ms`（默认 720 ms）内检测到新
语音，则继续复用原 `key` 合并识别，否则发送一次对应的 `finished=true`。达到最大段长时
仍立即保护切段，请求和响应格式保持不变。

### WebSocket v1.0.3（SenseVoice 实验接口）

- **路径**：`/v1.0.3/seacraft_asr_online`
- **直连地址**：`ws://<host>:8084/v1.0.3/seacraft_asr_online`
- **上行和基础响应字段**：与 v1.0.2 相同。
- **临时结果**：继续使用在线 Paraformer；VAD 候选 end、稳定新增 8 字或累计新增
  4.8 秒音频时，会异步提交当前完整语句给 SenseVoiceSmall。纠正结果仍使用原 `key`
  并返回 `finished=false`。
- **最终结果**：VAD 断句确认后复用相同边界的 SenseVoice 结果；没有可复用结果时提交
  final 任务。超时或模型失败时使用当前可见文本有界完成，不让字幕永久停在
  `finished=false`。
- **标点语义**：使用 SenseVoiceSmall 自带标点，不调用独立 PUNC 模型。

中间纠正与字幕完成状态相互独立。`finished=true` 仍只表示该字幕段真正结束，不会因为
一次中间纠正或一次尚未确认的 VAD end 提前发送。每个会话最多有一个纠正任务在途，
后续快照采用 latest-only；连接、`key`、revision、文本版本或样本边界不匹配的旧结果会
被丢弃。

```toml
# 是否在最终断句前执行中间纠正；false 时仍保留句末 SenseVoice 纠正。
progressive_correction_enabled = true
progressive_correction_chars = 8
progressive_correction_audio_ms = 4800
progressive_correction_cooldown_ms = 2000

# 这是 SenseVoice correction worker，不是 Uvicorn worker。
# 每个 worker 独占一个模型实例，增加该值会线性增加模型显存需求。
sensevoice_correction_workers = 1
correction_queue_capacity = 16
correction_task_timeout_ms = 5000
```

Uvicorn 必须继续使用 `--workers 1`。`sensevoice_correction_workers` 可单独测试
`1/2/4/6`；任一模型实例加载失败或 CUDA OOM 时服务会启动失败，不会静默减少 worker
数量。队列满时允许合并或跳过中间纠正，但 final 会有界等待并保证完成语义。

SenseVoice 模型通过 `config.toml` 配置并从项目内加载：

```toml
[model_paths]
sensevoice_model_dir = "model/SenseVoiceSmall"
```

### WebSocket v1.0.4（累计 Paraformer 纠正接口）

- **路径**：`/v1.0.4/seacraft_asr_online`
- **直连地址**：`ws://<host>:8084/v1.0.4/seacraft_asr_online`
- **协议**：上行仍是 16 kHz、单声道、int16 PCM；下行字段严格保持
  `key/text/finished/bg/ed`。
- **三模型**：Online Paraformer 生成增量初稿；普通非流式 Paraformer-large
  识别当前段累计 PCM；独立 CT-Transformer 补标点。均使用 PyTorch，v4 不使用 SenseVoice。
- **句中纠正**：首次累计至少 4 秒，此后每新增至少 4 秒且有新文字允许再次提交。
  0.48 秒块通常在 4.32、8.64、12.96 秒触发。忙时只保留一个最新待处理水位，
  执行时冻结最新累计音频，不复制多份 pending PCM；周期纠正始终 `finished=false`。
- **断句**：保留连续两个在线空结果及 20 秒/200 字保护规则，不增加 VAD。
  在线空输出不代表真实静音。只在段末 flush 在线缓存，并发送唯一 `finished=true`。
- **字幕顺序**：仍返回稳定 `key`，但消费者只依赖 `finished` 分段也能使用。
  单一协调器发送全部响应，旧段结束后不会收到其迟到纠正。
- **句末预算**：最终完整快照有独立机会。进入收尾后最多等待 100 ms，包括排队和
  纠正标点；在线 flush 本身必须完成，耗尽预算时不再额外等待。在线积压时不等待。
  回退保留已纠正前缀和所有在线尾部，不要求最终标点必定就绪。

前缀衔接使用规范化文字及原始位置映射，数字格式统一后寻找 4～12 个字符的唯一末尾锚点，
并限制在快照文字水位前后 24 个规范化字符内。这是文本对齐，不是字级时间戳。
无唯一锚点时暂缓该次，后续在线出字可再次验证，后续累计纠正仍可执行。
纯标点、长度不足原文 55% 的明显截断、过度扩张和大范围无关替换受到保护；
最终结果若能定位到遗漏至少 4 字的尾部也会被拒绝。这些规则不保证识别准确率。

每会话一个在途任务、一个 pending 水位，全局 pending 默认最多 8 个（含忙碌会话）。
接收缓冲最多 20 秒 PCM、64 帧，超限明确关闭连接，不静默丢帧。
线程超时或会话断开不会释放仍在执行的模型；超过 30 秒的活动纠正会阻止新准入，
v4 会话记录 `v4_model_stalled` 并关闭，需要检查日志并受控重启。
标点使用 v4 专属串行入口，旧三个接口的模型和调用分支不变。
标点重算前移除已有的常见句读、保留小数和时间分隔，避免对纠正基底重复加出 `。，` 等组合。

```toml
device = "cuda:0"
ngpu = 1
v4_correction_device = "cuda:1"
v4_punctuation_device = "cuda:1"
v4_correction_workers = 1
v4_correction_interval_ms = 4000

v4_final_budget_ms = 100
v4_max_segment_seconds = 20
v4_partial_punctuation_chars = 8
v4_partial_punctuation_interval_ms = 2000
v4_correction_queue_capacity = 8

[model_paths]
v4_correction_model_dir = "model/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
```

Mac 的默认配置使用 CPU。server12 项目已迁移到 `/home/zhangs/workspace/asr_online`：

```bash
conda activate asr
cd /home/zhangs/workspace/asr_online
CONFIG_PATH=test/config.server12-v4.toml python -m uvicorn app.main:app --host 0.0.0.0 --port 8084 --workers 1
```

短片段脚本 `test/validate_cumulative_v4.py` 在服务器本机连接 8084，实时发送教师音频
98 秒起的 30 秒片段；独立目录保留完整 JSONL、音频及纠正位置，脚本退出时关闭其后端。
运行方式见脚本说明。此次仅验证短片段功能，准确率与纠正好坏待用户核验，不提供容量结论。

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

WebSocket 测试可参考 `test/test_api_ws_client.py`（需自备 16 kHz PCM/WAV 测试音频）。默认 `--send-mode fast` 按服务端 `ed` 推进加速发送，最多领先 2.88 秒音频；`--window-seconds` 可在 0.48~9.6 秒内调整，低于服务端 20 秒缓存上限。纠正响应的重复 `ed` 不释放发送额度。实际速度取决于服务端处理能力，加速测试不等同于实时输入下的纠正效果测试；模拟实时输入使用 `--send-mode realtime`，每 0.48 秒发送一块。所有流式及纠正响应独立接收保存。默认音频处理进度 30 秒未推进即退出，可通过 `--response-timeout` 调整；断连会报告关闭码和原因。成功、失败或中断均保存 `.summary.json`，包含发送模式、窗口、发送块数、已发送音频时长（包含末块补齐和尾部静音）、最后响应 `ed` 和退出状态。`--output` 指定 JSONL 路径，重复使用同一路径会覆盖旧结果。

## Docker 构建与运行

镜像基于 `nvcr.io/nvidia/cuda:12.1.1-cudnn8-runtime-centos7`，内含 Conda 环境 `asr`。
使用 CUDA 时需要 NVIDIA GPU 与 NVIDIA Container Toolkit。完整构建、单独启动和接入平台的
命令见 [`docker/README.md`](docker/README.md)。

### 构建镜像

在项目根目录执行，构建上下文仍是项目根目录：

```bash
cd /home/zhangs/workspace/asr_online

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

镜像已经包含项目根 `model/`。运行时必须挂载实际 `config.toml`；仅在配置显式使用镜像外模型
路径时才需要额外挂载模型目录：

```bash
docker run -d \
  --name seacraft-asr-online \
  --gpus all \
  -p 8084:8084 \
  -v /path/on/host/config.toml:/config.toml:ro \
  -e CONFIG_PATH=/config.toml \
  -e UVICORN_WORKERS=1 \
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
# 日志

运行日志默认写入 `logs/{instance_id}/application.log`，同时输出到 stdout；单文件上限
100 MiB，归档保留 7 日。日志不记录 PCM、完整转写文本、WebSocket 消息体或凭据。容器不
挂载日志卷时仍可在容器内查看；跨重建持久化使用可选日志 Compose 覆盖层。

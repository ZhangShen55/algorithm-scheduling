# SeaCraftASR - 智能语音转写与分析服务

基于 FastAPI 搭建的企业级语音处理后端服务，专注于教育场景（课堂录音分析），提供多维度语音AI能力。

## 🎯 核心功能

| 功能模块 | 描述 | 技术方案 |
|---------|------|---------|
| **ASR 转写** | 语音转文字 | Paraformer(`auto`/`zh`/`en`) + Faster-Whisper(`fr`) |
| **说话人分离** | 区分不同说话人 | CAM++（Paraformer 路径） |
| **情感识别** | 分析语音情绪 | emotion2vec（Paraformer 路径） |
| **五何分类** | 教师提问分类 | BERT 文本分类 |
| **角色识别** | 自动识别教师/学生 | 特征工程 + 规则引擎 |

## 🏗️ 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                        API 路由层                            │
├─────────────────────────────────────────────────────────────┤
│  /v1.1.8/seacraft_asr    │  离线ASR（中英文 + 法语）        │
│  /text/question          │  五何分类分析                   │
│  /audio/db_snr           │  音频质量分析                   │
│  /get_status             │  服务状态监控                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        核心层 (Core)                         │
├─────────────────────────────────────────────────────────────┤
│  config.py    │  配置管理（支持热更新）                      │
│  models.py    │  AI模型懒加载与管理                          │
│  concurrency.py  │  GPU并发控制（信号量机制）                  │
│  logging.py   │  日志配置与管理                              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       模型层 (Models)                        │
├─────────────────────────────────────────────────────────────┤
│  Paraformer  │  Whisper  │  emotion2vec  │  BERT  │  CAM++  │
└─────────────────────────────────────────────────────────────┘
```

## 📁 项目结构

```
asr_offline/
├── app/
│   ├── main.py                # FastAPI 入口，导出 app
│   ├── api/routes/
│   │   ├── asr_v18.py         # 唯一离线 ASR 路由
│   │   ├── asr_common.py      # ASR 公共处理
│   │   ├── text.py            # 五何分类
│   │   ├── audio.py           # 音频质量分析
│   │   └── status.py          # 状态监控
│   ├── core/
│   │   ├── config.py          # 配置管理
│   │   ├── models.py          # 模型加载
│   │   ├── concurrency.py     # GPU 并发控制
│   │   └── logging.py         # 日志配置
│   ├── entity/
│   │   └── data.py            # 数据模型
│   └── utils/
│       ├── audio_utils.py     # 音频处理工具
│       ├── feature_utils.py   # 特征提取
│       └── asr_stats.py       # 统计信息
├── config.toml               # 配置文件（TOML 格式）
├── requirements.txt          # 依赖包
├── docker/                   # 容器化部署文件
│   ├── Dockerfile           # 镜像构建
│   └── start.sh             # 单 Uvicorn、workers=1 启动脚本
└── test_wav/                 # 本地验证音频
```

## 🚀 快速开始

### 环境要求

- Python 3.11
- CUDA 11.8+ (GPU模式)
- 16GB+ 内存
- 50GB+ 磁盘空间（模型文件）

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置

配置文件为 `config.toml`（TOML 格式）。服务通过环境变量 `CONFIG_PATH` 指定配置路径，未设置时默认读取当前目录下的 `./config.toml`。

```toml
# 基础配置
id_engine = "1"
version = "seacraft-asr-app-v1.1.9"

# 设备与并发配置
device = "cuda:1"          # 推理设备
ngpu = 1                   # GPU 数量
ncpu = 4                   # CPU 线程数
concurrency = 5            # 单实例 GPU 并发数

# 日志配置
log_path = "./asr_service.log"

# 热词文件路径
hotword_path = "/var/model_zoo/model_asr/.../hotword.txt"

# 模型路径配置
[model_paths]
vad_model_dir = "/var/model_zoo/model_asr/speech_fsmn_vad_zh-cn-16k-common-pytorch"
punc_model_dir = "/var/model_zoo/model_asr/punc_ct-transformer_cn-en-common-vocab471067-large"
asr_model_dir = "/var/model_zoo/model_asr/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
spk_model_dir = "/var/model_zoo/model_asr/speech_campplus_sv_zh_en_16k-common_advanced"
emotion_model_dir = "/var/model_zoo/model_asr/emotion2vec_plus_large"
whisper_model_dir = "/var/model_zoo/model_asr/faster-whisper-large-v3"
bert_model_tokenizer = "/var/model_zoo/model_asr/bert-base-chinese"
bert_model_dir = "/var/model_zoo/model_asr/bert_output/checkpoint-88"

# 计算配置（faster-whisper）
[compute]
compute_type = "int8"      # int8 / float16 等

# 语速计算配置
[speech_rate]
rate_factor = 0.4          # 单句语速修正系数（数值偏高时下调）

# 功能开关配置
[features]
open_spk = true            # Paraformer 路径说话人分离
open_emotion = true        # Paraformer 路径情感识别
ban_hotword = true         # 禁用热词
open_mul_lang = true       # 法语 Faster-Whisper 转写
open_fivewh = true         # 五何分类(/text/question)
```

> 实时转写（WebSocket）已拆分为独立项目 `jy-algorithm-app-asr-online`，不在本仓库内提供。

### 启动服务

```bash
# 开发模式
python -m uvicorn app.main:app --host 0.0.0.0 --port 8083 --workers 1

# 生产模式（单端点，多实例由多容器注册）
CONFIG_PATH=./config.toml bash docker/start.sh
```

## 📡 API 使用示例

### 离线ASR转写

`POST /v1.1.8/seacraft_asr` 是唯一离线 ASR 接口。服务先对 `language` 去除首尾空白并转为小写，再按下表路由：

| `language` | 转写引擎 | 说明 |
|---|---|---|
| `auto`、`zh`、`en` | Paraformer | 保留现有说话人、角色和情绪分析能力 |
| `fr` | Faster-Whisper | 当前唯一小语种白名单值，不调用说话人、角色或情绪模型 |

`fr` 只有在 `[features].open_mul_lang=true` 且 Whisper 模型就绪时才执行。功能关闭或模型未就绪时仍返回 HTTP 200，响应体为 `{"msg":"未开启小语种识别或模型未就绪","code":4003}`。空值或其他未支持的语言同样返回 HTTP 200，业务码为 `4009`。

```bash
curl -X POST "http://localhost:8083/v1.1.8/seacraft_asr" \
  -F "audioFile=@test.wav" \
  -F "language=auto" \
  -F "showSpk=true" \
  -F "showEmotion=true"
```

响应示例：

```json
{
  "language": "auto",
  "segments": [
    {
      "segment_text": "如果与中文相比，",
      "bg": "0.17",
      "ed": "1.13",
      "speed": 230,
      "segment_words": [],
      "role": "teacher",
      "emotion": "平淡"
    }
  ],
  "text": "如果与中文相比，...",
  "speed_info": [
    { "unit": 1,  "segment_info": { "segment_count": 45, "speed": [237, 220] } },
    { "unit": 5,  "segment_info": { "segment_count": 9,  "speed": [237, 220] } },
    { "unit": 10, "segment_info": { "segment_count": 5,  "speed": [237, 220] } }
  ],
  "load_audio_time_ms": "163.24",
  "gpu_time_ms": "1349.49"
}
```

响应字段说明：

| 字段 | 说明 |
|------|------|
| `segments[].role` | Paraformer 路径按现有能力返回；`fr` 仅在 `showSpk=true` 或 `showRoleIdentify=true` 时返回 `null`，两者均为 `false` 时不返回该字段 |
| `segments[].emotion` | Paraformer 路径按现有能力返回；`fr` 仅在 `showEmotion=true` 时返回 `null`，为 `false` 时不返回该字段 |
| `segments[].segment_words` | 每段始终存在；`wordTimestamps=false` 时为 `[]`，为 `true` 时返回真实词时间，个别无法对齐的段允许为 `[]` |
| `segments[].speed` | **单句语速**（字/分钟），计算式为 `int(内容数量 × 60 / (ed-bg) × 0.4)`。中文按字计数，英文、法语等使用 Unicode 单词计数，数字串各计 1；结果不受 `wordTimestamps` 开关影响 |
| `speed_info` | **分时段语速统计**，按 1/5/10 分钟三种窗口单位分别统计 |
| `speed_info[].unit` | 时间窗口单位（分钟） |
| `speed_info[].segment_info.segment_count` | 该单位下切出的时间窗口个数（= `speed` 数组长度） |
| `speed_info[].segment_info.speed` | 每个时间窗口的语速（字/分钟）列表 |

成功响应顶层保持既有字段 `language`、`segments`、`text`、`speed_info`、`load_audio_time_ms` 和 `gpu_time_ms`，不会增加能力状态或成功业务码字段。

#### `speed_info` 分时段语速计算方式

对 1 / 5 / 10 分钟三种窗口单位，分别独立按下述步骤计算（`unit` 为窗口分钟数）：

**① 划分时间窗口**

以时间轴 `0` 秒为起点，按 `unit×60` 秒等分。设整段最后一句的结束时间为 `max_end`（秒），则窗口个数：

```
segment_count = ceil(max_end / (unit×60))
```

第 `k` 个窗口（`k` 从 0 开始）覆盖时间区间 `[k×unit×60, (k+1)×unit×60)`。

**② 统计每句的"实际内容字数"**

去除标点、空格等无关内容后计数：中文按字，英文、法语等按 Unicode 单词计数，数字串各计 1（与单句 `speed` 同口径）。

```
words(句子) = 中文字数 + 英文单词数 + 数字串个数
```

**③ 跨窗口的句子按时间重叠比例拆分**

若一句 `[bg, ed]` 跨越多个窗口，则把它的字数按"与各窗口的重叠时长 / 该句总时长"的比例分摊到对应窗口：

```
overlap(句子, 窗口k) = min(ed, 窗口k末) − max(bg, 窗口k首)
窗口k获得的字数 += words(句子) × overlap(句子, 窗口k) / (ed − bg)
```

**④ 计算每个窗口的语速**

分母为窗口的**标称时长（固定为 unit 分钟）**，因此窗口内停顿（空闲）越多，语速越低：

```
窗口语速 = 窗口内累计字数 / (窗口标称时长 / 60)
```

补充规则：

- **末窗口**：最后一个不足 `unit` 的窗口，标称时长改用**实际剩余时长** `max_end − k×unit×60`，避免被整窗时长稀释。
- **空窗口**：窗口内完全没有说话内容时，语速记为 `0`。
- 该统计**不**乘 `rate_factor`（与单句 `speed` 不同：空闲已通过固定分母体现）。

**计算示例**（`unit=1`，即 60 秒窗口）：

某句 `bg=58s, ed=63s`、字数 10，跨第 0、1 两个窗口：

- 与窗口0（0–60s）重叠 2 秒 → 分到 `10 × 2/5 = 4` 字；
- 与窗口1（60–120s）重叠 3 秒 → 分到 `10 × 3/5 = 6` 字。

若窗口0内另有一句贡献 10 字，则窗口0共 14 字，其语速 = `14 / (60/60) = 14`（字/分钟）。

## ⚙️ 功能开关说明

以下开关均位于 `config.toml` 的 `[features]` 段下（下表默认值为示例 `config.toml` 的取值；代码缺省值除 `ban_hotword` 外均为 `false`）：

| 配置项 | 说明 | 示例值 |
|-------|------|--------|
| `open_spk` | 开启 Paraformer 路径说话人分离 | `true` |
| `open_emotion` | 开启 Paraformer 路径情感识别 | `true` |
| `open_mul_lang` | 开启法语 Whisper 转写；关闭或模型未就绪时 `language=fr` 返回 HTTP 200、`code=4003` | `true` |
| `open_fivewh` | 开启五何分类(`/text/question`) | `true` |
| `ban_hotword` | 禁用热词功能 | `true` |

## 🐳 Docker 部署

### 构建前准备

1. 确保存在 `wheel/algorithm_operator_registry_client-0.1.0-py3-none-any.whl`；PyArrow 20.0.0 由 Dockerfile 直接通过 pip 安装。
2. 确保项目根目录存在 `model/`，镜像会把 Paraformer、Whisper、CAM++、emotion2vec 和 FiveWh 所需模型直接打入镜像，不需要运行时挂载模型目录。
3. 基础镜像为 **CentOS 7**（`glibc 2.17`），Dockerfile 已固定 `Miniconda3-py311_23.11.0-2`，勿改用 `Miniconda3-latest`（会报 `GLIBC >=2.28`）。
4. 构建上下文通过 `.dockerignore` 排除 `tests/`、`test_wav/`、日志及已退役的小语种说话人模型目录；其余运行所需权重仍进入镜像。

### 构建镜像

镜像为三阶段：`deps`（依赖）→ `obfuscator`（PyArmor 代码混淆）→ `runtime`（运行）。

```bash
cd /path/to/asr_offline
docker build -f docker/Dockerfile -t seacraft-asr .
```

`obfuscator` 阶段会将完整 `app/` Python 包生成为 PyArmor 混淆产物；运行层只复制混淆后的业务代码。

镜像内模型固定放在 `/app/model`，同时创建兼容软链：

- `/model` → `/app/model`
- `/var/model_zoo/model_asr` → `/app/model`

因此宿主机挂载的旧 `config.toml` 如果仍使用 `/var/model_zoo/model_asr/...`，容器内会解析到镜像内置模型。

### 运行容器

容器直接暴露单个 Uvicorn **8083** 端点，并固定 `workers=1`。多 GPU 或多实例通过启动多个容器实现，每个容器使用独立端口和平台实例标识。运行时只挂载宿主机 `config.toml`，不挂载模型目录。

```bash
docker run -d \
  --name seacraft-asr \
  --gpus '"device=1"' \
  -p 8083:8083 \
  -v /path/to/config.toml:/config.toml:ro \
  -e CONFIG_PATH=/config.toml \
  seacraft-asr
```

| 项 | 说明 |
|----|------|
| `-p 8083:8083` | 独立离线 ASR Uvicorn 端点 |
| `CONFIG_PATH` | 与 `core/config.py`、start.sh 共用，须为 TOML |
| 模型目录 | 已内置在镜像 `/app/model`，无需挂载 |

## 📊 监控指标

访问 `/get_status` 获取服务状态：

```json
{
  "id_engine": "1",
  "status": "living",
  "appVersion": "seacraft-asr-app-v1.1.9",
  "runTime": "1天 2小时 30分",
  "totalHaveDoneProcessTasks": 1523,
  "totalFailedTasks": 12,
  "offlineDone": 1200,
  "onlineDone": 323
}
```

## 🤝 贡献

欢迎提交 Issue 和 Pull Request。

## 📄 License

MIT License

# PP-OCRv6 FastAPI 服务

本项目使用 PaddleOCR 3.7、PP-OCRv6 medium 和 Paddle 官方公式模型提供普通文字 OCR 与可选公式识别服务。它是独立的新服务，不依赖 Paddle Serving，也不修改原有 PP-OCRv4 服务。

服务在同一端口提供：

- `POST /ocr/prediction`
- `GET /ocr/getVersion`

OCR 接口使用 `key`、`value` 平行数组：`key[i]` 是图片 ID，`value[i]` 是对应图片的 Base64。单次请求可提交多张图片，图片数据支持纯 Base64 和 `data:image/<格式>;base64,...`。成功响应原样返回图片 ID，每个普通 OCR 结果仍保持为 JSON 字符串。请求可通过 `enable_formula` 启用公式识别，公式结果按图片写入 `formula_results`；公式识别不会改写普通 OCR 的 `key`、`value` 或其中的疑似公式文本。

## 项目结构

```text
app/
├── api/routes/          # HTTP 路由
├── core/                # 配置、日志和异常
├── engines/             # PaddleOCR 引擎与抽象接口
├── schemas/             # 请求、响应模型
├── services/            # 普通 OCR 与公式识别编排
├── utils/               # 图片处理
└── main.py              # 应用工厂和统一启动入口
models/                  # 普通 OCR、版面定位和公式识别模型及摘要清单
docker/                  # CPU/GPU 与 NPU 构建文件
tests/                   # 契约、单元和集成测试
scripts/                 # 模型校验和服务冒烟测试
docs/                    # 接口与部署说明
config.toml              # 已提交的本地安全默认配置
config.toml.example      # 可复制的配置示例
```

## 本地 CPU 环境

推荐使用 Python 3.11：

```bash
conda create -n ocr-v6 python=3.11 -y
conda activate ocr-v6
python -m pip install -r requirements.cpu.txt
```

准备配置并校验模型：

```bash
cp config.toml.example config.toml
python scripts/verify_models.py
```

根 `config.toml` 随仓库提交并保持本地安全默认值；部署时可只读挂载另一份配置。模型路径相对于所选配置文件解析。本地 CPU 配置保持：

```toml
[ocr]
device = "cpu"

[formula]
enabled = false
layout_model_dir = "models/PP-DocLayout_plus-L"
recognition_model_dir = "models/PP-FormulaNet_plus-M"
recognition_batch_size = 1
layout_threshold = 0.5
```

### 平台注册与运行配置

受控 GPU 部署使用 `algorithm-scheduling-platform/deploy/config/operators/ocr.gpu.toml`：

| 字段 | 本地根配置 | 受控部署 | 说明 |
| --- | --- | --- | --- |
| `platform.registration_enabled` | `false` | `true` | 是否主动注册到调度平台 |
| `platform.control_service_url` | `""` | `http://control-service:18100` | 注册与心跳地址 |
| `platform.heartbeat_interval_seconds` | `5` | `5` | 心跳间隔 |
| `platform.max_concurrent_requests` | `256` | `256` | 单实例平台图片请求容量 |
| `runtime.require_gpu` | `false` | `true` | GPU 受控部署失败关闭开关 |

Compose 继续管理实例 ID、服务 URL、注册 Token、物理 GPU/可见设备、`CONFIG_PATH`、端口和
`UVICORN_WORKERS=1`。平台容量 `256` 不替代 `ocr.max_concurrency=1` 和引擎锁；后两者继续保证
单引擎串行安全。根配置和受控部署都使用 `ocr.image_max_bytes=52428800`（单图 50 MiB）。

`[formula].enabled` 是服务端总开关，默认关闭；需要提供公式能力时改为 `true`。请求中的 `enable_formula` 是单次请求开关，两级均开启才会使用项目内 `PP-DocLayout_plus-L` 定位公式并由 `PP-FormulaNet_plus-M` 识别 LaTeX。

## 启动

从项目根目录执行：

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8866 --workers 1
```

启动入口读取 `config.toml` 中的 `server.host`、`server.port` 和 `server.workers`。默认监听 `0.0.0.0:8866`，并使用一个 worker，避免重复加载模型。

检查服务：

```bash
curl http://127.0.0.1:8866/ocr/getVersion
python scripts/smoke_test.py
```

服务端已开启公式能力时，追加公式冒烟验证：

```bash
python scripts/smoke_test.py \
  --enable-formula \
  --image tests/fixtures/formula-document.png
```

指定其他服务地址或测试图片：

```bash
python scripts/smoke_test.py \
  --base-url http://127.0.0.1:8866 \
  --image tests/fixtures/ocr-test.jpg
```

## 压测

按固定请求总数压测：

```bash
python scripts/load_test.py \
  --ip 127.0.0.1 \
  --port 8866 \
  --image tests/fixtures/ocr-test.jpg \
  --concurrency 10 \
  --requests 1000 \
  --output reports/load-test.json
```

按固定持续时间压测：

```bash
python scripts/load_test.py \
  --ip 127.0.0.1 \
  --port 8866 \
  --image tests/fixtures/ocr-test.jpg \
  --concurrency 10 \
  --duration 60
```

`--requests` 和 `--duration` 二选一；均未设置时默认发送 100 个请求。脚本输出成功率、QPS、平均耗时、P50、P90、P95、P99、最小和最大耗时。需要压测公式识别时追加 `--enable-formula`。存在失败请求时进程退出码为 `2`。

## 测试

默认运行契约、单元和 Mac CPU 集成测试：

```bash
pytest -m "not gpu and not npu"
```

只运行快速测试：

```bash
pytest tests/contract tests/unit
```

CPU 集成测试会执行普通 OCR 和公式端到端推理：

```bash
pytest -m cpu
```

Mac CPU 上一次真实公式集成测试约耗时 86 秒，该记录包含首次加载普通 OCR、版面定位、公式识别共四个模型以及单次推理，只用于说明测试开销，不是吞吐量、延迟或其他性能指标。NVIDIA GPU 和 Ascend NPU 的公式能力仍须在对应真实硬件与运行时中验收。

## Docker

CPU 与 NVIDIA GPU 共用 `docker/Dockerfile`，Ascend NPU 使用 `docker/Dockerfile.npu`。正式配置必须从宿主机只读挂载到 `/app/config.toml`。完整命令见 [docker/README.md](docker/README.md)。

## 维护边界

- 普通 OCR 固定使用项目内 `PP-OCRv6_medium_det` 和 `PP-OCRv6_medium_rec`；公式能力固定使用项目内 `PP-DocLayout_plus-L` 和 `PP-FormulaNet_plus-M`。
- 模型缺失或摘要不一致时不得部署，运行时不自动下载模型。
- `cpu`、`cuda:<编号>`、`npu:<编号>` 共用业务代码；设备不可用时启动失败，不回退 CPU。NVIDIA 配置会在引擎适配层转换为 Paddle 使用的 `gpu:<编号>`。
- 默认单 worker、单引擎实例。扩容优先增加容器实例。
- 修改接口前先运行契约测试；响应中的每个 `value[i]` 必须保持为 JSON 字符串，并与 `key[i]` 对应。

接口字段见 [docs/接口兼容说明.md](docs/接口兼容说明.md)，跨硬件状态见 [docs/部署说明.md](docs/部署说明.md)。

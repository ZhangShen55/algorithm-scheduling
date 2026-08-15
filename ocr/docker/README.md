# OCR v6 Linux Docker 部署

本文面向已经取得 `ocr_v6_amd.tar` 的 Linux 运维人员。最终交付镜像为
`linux/amd64` Cython 保护版本 `ocr:v6_amd`。正式 `config.toml` 不在镜像中，必须由
宿主机只读挂载。Cython 用于提高源码阅读和逆向门槛，不是密码学加密。

## 1. 检查环境

```bash
uname -m
docker version --format '{{.Server.Version}}'
nvidia-smi -L
docker run --rm --gpus '"device=2"' nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi -L
```

主机必须为 `x86_64`，Docker、NVIDIA 驱动和 NVIDIA Container Toolkit 必须可用。
本文以物理 GPU 2（RTX 3090）为例。

## 2. 校验并加载离线镜像

交付目录至少包含 `ocr_v6_amd.tar`、`ocr_v6_amd.tar.sha256` 和配置示例。执行：

```bash
cd /opt/ocr-v6
sha256sum -c ocr_v6_amd.tar.sha256
docker load -i ocr_v6_amd.tar
docker image inspect ocr:v6_amd --format '{{.Id}} {{.Architecture}} {{.Os}}'
```

校验必须输出 `ocr_v6_amd.tar: OK`，镜像架构必须为 `amd64 linux`。摘要不一致时停止
加载并重新传输 tar 包。

## 3. 创建宿主机配置

在 `/opt/ocr-v6/config.toml` 写入：

```toml
[application]
name = "ocr"
version = "OCR_V3.0_PP-OCRv6"

[server]
host = "0.0.0.0"
port = 8866
workers = 1

[ocr]
# 物理 GPU 2 单独映射后，在容器中编号为 0
device = "cuda:0"
# 以下两个字段仅 CPU 模式生效
cpu_threads = 8
enable_mkldnn = false
detection_model_dir = "models/PP-OCRv6_medium_det"
recognition_model_dir = "models/PP-OCRv6_medium_rec"
# RTX 3090 实测推荐值
recognition_batch_size = 4
# 当前镜像未安装 UltraInfer
enable_hpi = false
# 单引擎串行推理，并发请求在入口排队
max_concurrency = 1
image_max_bytes = 20971520

[ocr.detection]
limit_side_len = 960
threshold = 0.3
box_threshold = 0.5
unclip_ratio = 1.5

[formula]
enabled = false
layout_model_dir = "models/PP-DocLayout_plus-L"
recognition_model_dir = "models/PP-FormulaNet_plus-M"
recognition_batch_size = 1
layout_threshold = 0.5

[logging]
level = "INFO"
directory = "logs"
max_size_mb = 100
backup_count = 3
```

关键参数：

| 参数 | 说明 |
| --- | --- |
| `device` | `cpu`、`cuda:<容器逻辑编号>` 或已适配环境中的 `npu:<容器逻辑编号>` |
| `recognition_batch_size` | 单次送入识别模型的文本区域数；增大可能提高吞吐，也会增加显存 |
| `enable_hpi` | Paddle 高性能推理开关；本镜像保持 `false` |
| `max_concurrency` | 同时进入推理流程的请求数；单引擎部署保持 `1` |
| `limit_side_len` | 检测前缩放边长上限；越大越利于小字，也更耗时 |
| `threshold`、`box_threshold` | 文本像素候选阈值和文本框置信度阈值 |
| `unclip_ratio` | 文本框向外扩张比例 |
| `formula.enabled` | 公式识别服务端总开关 |
| 公式 `recognition_batch_size` | 公式识别批量，本次固定为 `1` |

## 4. 启动容器

```bash
docker rm -f ocr-v6-amd 2>/dev/null || true
docker run -d \
  --name ocr-v6-amd \
  --restart unless-stopped \
  --gpus '"device=2"' \
  -e REQUIRE_GPU=true \
  -p 8866:8866 \
  -v "/opt/ocr-v6/config.toml:/app/config.toml:ro" \
  --log-driver json-file \
  --log-opt max-size=100m \
  --log-opt max-file=3 \
  ocr:v6_amd
```

`--gpus '"device=2"'` 只暴露物理 GPU 2，因此配置使用逻辑 `cuda:0`。`REQUIRE_GPU=true`
保留平台的 GPU 启动门禁。查看状态和日志：

```bash
docker ps --filter name=ocr-v6-amd
docker logs --tail 200 -f ocr-v6-amd
```

### 启动成功日志

容器必须保持 `Up`，日志至少包含：

```text
Application startup complete.
Uvicorn running on http://0.0.0.0:8866
```

RTX 3090 验收时还应看到检测和识别模型初始化日志。

## 5. 验证服务

```bash
docker exec ocr-v6-amd nvidia-smi -L
docker exec ocr-v6-amd python -c 'import paddle; print(paddle.__version__); print(paddle.device.get_device())'
docker exec ocr-v6-amd sh -c 'cd /app/models && sha256sum -c manifest.sha256'
curl --fail --show-error http://127.0.0.1:8866/ocr/getVersion
python3 /opt/ocr-v6/smoke_test.py \
  --base-url http://127.0.0.1:8866 \
  --image /opt/ocr-v6/ocr-test.jpg
```

容器内只应列出一张 RTX 3090，编号为 GPU 0；Paddle 设备应为 `gpu:0`。配置中
`formula.enabled = false` 时，请求 `enable_formula=true` 会在 `formula_results` 返回未启用
信息。要验证真实公式识别，先将服务端开关设为 `true`，重启后执行：

```bash
python3 /opt/ocr-v6/smoke_test.py \
  --base-url http://127.0.0.1:8866 \
  --image /opt/ocr-v6/formula-document.png \
  --enable-formula
```

检查 Cython 最终镜像不包含核心源码和构建工具：

```bash
docker run --rm --entrypoint sh ocr:v6_amd -c '
  test -n "$(find /app/app -type f -name "*.so" -print -quit)" &&
  test -z "$(find /app/app -type f -name "*.py" ! -name "__init__.py" -print -quit)" &&
  test -z "$(find /app -type f \( -name "*.c" -o -name "*.cpp" -o -name "*.o" \) -print -quit)" &&
  test ! -e /app/config.toml &&
  test ! -e /app/.build &&
  ! command -v gcc &&
  ! python -m pip show Cython
'
```

RTX 3090 固定图片矩阵覆盖 OCR batch `1/4/8/16` 和客户端并发 `1/2/4/8/16`，20 组
均为 100% 成功且无 HTTP 5xx。推荐 `recognition_batch_size = 4`、客户端并发 `2`，独立
复验为 `13.468 QPS`、P95 `152.716 ms`。完整数据见
`docs/ocr-v6-rtx3090-benchmark.md`。

## 6. 常见失败日志

### 配置文件不存在

```text
app.core.exceptions.ConfigurationError: 配置文件不存在：/app/config.toml
```

使用 `docker inspect ocr-v6-amd --format '{{json .Mounts}}'` 检查只读挂载。

### GPU 不可用

```text
app.core.exceptions.ConfigurationError: GPU 设备 cuda:0 不可用
```

使用 `nvidia-smi -L`、`docker inspect ocr-v6-amd --format '{{json .HostConfig.DeviceRequests}}'`
和 `docker exec ocr-v6-amd nvidia-smi -L` 核对物理卡、映射和容器逻辑编号。

### 模型缺失或摘要错误

```text
app.core.exceptions.ConfigurationError: 检测模型目录不存在：/app/models/not-found-det
```

模型校验还可能报告 `模型文件不存在`、`模型文件摘要不一致` 或 `模型清单缺少必需项`。
重新取得校验通过的 tar 包，不要在容器内替换单个模型文件。

### 端口占用

```text
Bind for 0.0.0.0:8866 failed: port is already allocated.
```

使用 `ss -lntp | grep ':8866'` 和
`docker ps --format '{{.ID}} {{.Names}} {{.Ports}}' | grep 8866` 定位占用方。

主动重启时可能出现 `FatalError: Termination signal`；如果随后重新出现
`Application startup complete.` 且接口通过，这是正常 SIGTERM 重启。

## 7. 日常运维

```bash
docker logs --tail 200 ocr-v6-amd
docker inspect ocr-v6-amd --format '{{.LogPath}}'
docker exec ocr-v6-amd tail -n 100 /app/logs/ocr-service.log
watch -n 1 nvidia-smi
docker restart ocr-v6-amd
docker stop ocr-v6-amd
docker rm ocr-v6-amd
```

## 8. 升级与回滚

升级前保留当前 tar、摘要文件和配置副本。回滚时精确停止并删除当前容器，重新加载保留的
tar，再使用第 4 节命令启动：

```bash
docker stop ocr-v6-amd
docker rm ocr-v6-amd
docker load -i /opt/ocr-v6/ocr_v6_amd.tar
```

不要删除 `/opt/ocr-v6/ocr_v6_amd.tar`，不得执行 `docker system prune`，也不得删除服务器上
任何 `algorithm*` 镜像。

## 9. 维护构建与 NPU

以下命令均从项目根目录执行。先准备正式配置：

```bash
cp config.toml.example config.toml
python scripts/verify_models.py
```

`config.toml` 已列入 `.gitignore` 和 `.dockerignore`；初始化 Git 后不会被默认跟踪，也不会进入 Docker 构建上下文，只能通过宿主机只读挂载进入容器。

需要提供公式能力时，在宿主机配置中启用项目本地公式模型：

```toml
[formula]
enabled = true
layout_model_dir = "models/PP-DocLayout_plus-L"
recognition_model_dir = "models/PP-FormulaNet_plus-M"
recognition_batch_size = 1
layout_threshold = 0.5
```

两个公式模型的运行文件合计约 716 MiB，已随项目模型目录进入构建上下文。服务端开关开启后，请求字段 `enable_formula` 还须设为 `true` 才执行公式推理。

## CPU/NVIDIA GPU 镜像

`docker/Dockerfile` 提供两种构建模式：

- 默认普通模式：保留 Python 源码，便于开发、排障和回滚；
- Cython 模式：显式传入 `--build-arg cython=yes`，核心功能模块编译为 Linux 原生扩展，最终镜像不保留对应 `.py` 源码。

Cython 只提高源码阅读和逆向门槛，不是密码学加密，也不代表产物不可逆向。源码仓库仍是开发、测试和代码审查的唯一来源。

普通 x86_64 Linux 镜像：

```bash
OCR_MODEL_MANIFEST_SECRET=/secure/build-inputs/ocr-runtime-manifest.sha256
docker build \
  --platform linux/amd64 \
  --secret id=ocr_model_manifest,src="$OCR_MODEL_MANIFEST_SECRET" \
  -f docker/Dockerfile \
  -t jy-ocr-v6-service:3.0-source \
  .
```

Cython 编译保护镜像：

```bash
OCR_MODEL_MANIFEST_SECRET=/secure/build-inputs/ocr-runtime-manifest.sha256
docker build \
  --platform linux/amd64 \
  --secret id=ocr_model_manifest,src="$OCR_MODEL_MANIFEST_SECRET" \
  --build-arg cython=yes \
  -f docker/Dockerfile \
  -t jy-ocr-v6-service:3.0-cython \
  .
```

不传 `cython` 与显式传入 `--build-arg cython=no` 等价。其他值会中止构建，例如：

```bash
docker build \
  --platform linux/amd64 \
  --build-arg cython=true \
  -f docker/Dockerfile \
  -t jy-ocr-v6-service:invalid \
  .
```

该命令必须报告 `cython must be "yes" or "no"`。镜像使用 Paddle 官方中文文档提供的 `ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddle:3.3.0-gpu-cuda11.8-cudnn8.9`。镜像体积较大，首次跨架构构建需要预留足够磁盘和下载时间。

`ocr_model_manifest` 是构建必需的 BuildKit secret。`OCR_MODEL_MANIFEST_SECRET` 必须是工作树和
外部模型根之外的临时投影文件；禁止在项目 `models/` 或外部 `ocr/models/` 内生成
`manifest.sha256`。调度平台发布应直接使用 `deploy/scripts/build-images`，由它从 Git
工作树外的权威 `model-assets.manifest.json` 投影 OCR 子集、设置 `0600` 权限并在构建
结束后清理。镜像会在依赖安装和应用导入前按该清单校验模型的精确文件集与
SHA-256，并仅保留运行时引擎需要的派生清单。

### CPU 运行

确认 `config.toml` 中：

```toml
[ocr]
device = "cpu"
```

启动：

```bash
docker run -d \
  --name ocr-v6 \
  -p 8866:8866 \
  -v "$(pwd)/config.toml:/app/config.toml:ro" \
  --log-driver json-file \
  --log-opt max-size=100m \
  --log-opt max-file=3 \
  jy-ocr-v6-service:3.0-source
```

Apple Silicon 运行该 x86_64 镜像时追加 `--platform linux/amd64`。该方式用于功能验收，不作为性能基准。

### 暴露全部 NVIDIA GPU

例如使用容器逻辑 GPU 2：

```toml
[ocr]
device = "cuda:2"
```

```bash
docker run -d \
  --name ocr-v6 \
  --gpus all \
  -p 8866:8866 \
  -v "$(pwd)/config.toml:/app/config.toml:ro" \
  --log-driver json-file \
  --log-opt max-size=100m \
  --log-opt max-file=3 \
  jy-ocr-v6-service:3.0-cython
```

### 只暴露一张物理 GPU

例如只暴露宿主机物理 GPU 2：

```bash
docker run -d \
  --name ocr-v6 \
  --gpus '"device=2"' \
  -p 8866:8866 \
  -v "$(pwd)/config.toml:/app/config.toml:ro" \
  --log-driver json-file \
  --log-opt max-size=100m \
  --log-opt max-file=3 \
  jy-ocr-v6-service:3.0-cython
```

此时物理 GPU 2 通常映射为容器逻辑 GPU 0，因此配置通常写 `device = "cuda:0"`。最终编号以容器内 `nvidia-smi -L` 为准。应用会在调用 PaddleOCR 和公式流水线时将 `cuda:<编号>` 转换为 Paddle 使用的 `gpu:<编号>`。

## NPU 镜像

NPU 镜像当前处于待确认状态。只有明确 Ascend 型号、驱动、CANN 基础镜像和 PaddleCustomDevice 兼容版本后，才能锁定 `requirements.npu.txt` 并执行：

```bash
docker build \
  --build-arg NPU_BASE_IMAGE=<已确认的-CANN-基础镜像> \
  -f docker/Dockerfile.npu \
  -t jy-ocr-v6-service:npu-3.0 \
  .
```

运行命令结构如下，尖括号内容必须按目标服务器官方容器部署说明替换：

```bash
docker run -d \
  --name ocr-v6-npu \
  <Ascend-设备映射与-CANN-运行参数> \
  -p 8866:8866 \
  -v "$(pwd)/config.toml:/app/config.toml:ro" \
  --log-driver json-file \
  --log-opt max-size=100m \
  --log-opt max-file=3 \
  jy-ocr-v6-service:npu-3.0
```

正式验收前配置为 `device = "npu:<容器逻辑编号>"`。当前命令是待补参数的结构，不可直接用于生产。

## 配置挂载检查

不挂载 `/app/config.toml` 时，容器应明确报错并退出：

```bash
docker run --rm jy-ocr-v6-service:3.0-source
docker run --rm jy-ocr-v6-service:3.0-cython
```

挂载有效配置后，应用读取其中的端口、设备和模型参数。模型相对路径以 `/app/config.toml` 所在目录为基准，因此示例中的 `models/...` 会解析为镜像内 `/app/models/...`。

## 接口与模型验证

查看启动日志：

```bash
docker logs -f ocr-v6
```

验证版本接口：

```bash
curl http://127.0.0.1:8866/ocr/getVersion
```

验证两个接口：

```bash
python scripts/smoke_test.py --base-url http://127.0.0.1:8866
```

宿主机配置已启用公式能力时，验证公式请求：

```bash
python scripts/smoke_test.py \
  --base-url http://127.0.0.1:8866 \
  --image tests/fixtures/formula-document.png \
  --enable-formula
```

Mac CPU 上一次包含首次四模型加载和推理的真实公式集成测试约 86 秒，该记录不是容器或生产环境性能指标。NVIDIA GPU 和 Ascend NPU 的公式能力仍须分别在对应真机、驱动和运行时中验证。

## 镜像内容检查

普通镜像应保留功能源码：

```bash
docker run --rm --entrypoint sh jy-ocr-v6-service:3.0-source -c '
  test -n "$(find /app/app -type f -name "*.py" ! -name "__init__.py" -print -quit)"
'
```

Cython 镜像应包含原生扩展，且不包含核心源码、编译中间产物、编译器、Cython、依赖清单、构建脚本或正式配置：

```bash
docker run --rm --entrypoint sh jy-ocr-v6-service:3.0-cython -c '
  test -n "$(find /app/app -type f -name "*.so" -print -quit)" &&
  test -z "$(find /app/app -type f -name "*.py" ! -name "__init__.py" -print -quit)" &&
  test -z "$(find /app -type f \( -name "*.c" -o -name "*.cpp" -o -name "*.o" \) -print -quit)" &&
  test ! -e /app/config.toml &&
  test ! -e /tmp/requirements &&
  test ! -e /app/.build &&
  ! command -v gcc &&
  ! python -m pip show Cython
'
```

两种镜像都可以用摘要文件重新校验模型：

```bash
docker exec ocr-v6 sh -c 'cd /app/models && sha256sum -c manifest.sha256'
```

## 停止与删除新容器

```bash
docker stop ocr-v6
docker rm ocr-v6
```

NPU 容器使用名称 `ocr-v6-npu`。这些操作只针对新服务，不涉及原有 OCR 容器。

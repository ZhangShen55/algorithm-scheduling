# Docker 构建与运行

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

## CPU/GPU 镜像

普通 x86_64 Linux 主机构建：

```bash
docker build \
  -f docker/Dockerfile \
  -t jy-ocr-v6-service:3.0 \
  .
```

Apple Silicon 只做跨架构构建时增加：

```bash
docker build \
  --platform linux/amd64 \
  -f docker/Dockerfile \
  -t jy-ocr-v6-service:3.0 \
  .
```

镜像使用 Paddle 官方中文文档提供的 `ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddle:3.3.0-gpu-cuda11.8-cudnn8.9`。镜像体积较大，首次构建需要预留足够磁盘和下载时间。

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
  jy-ocr-v6-service:3.0
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
  jy-ocr-v6-service:3.0
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
  jy-ocr-v6-service:3.0
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
docker run --rm jy-ocr-v6-service:3.0
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

在镜像内重新校验模型：

```bash
docker exec ocr-v6 python scripts/verify_models.py
```

## 停止与删除新容器

```bash
docker stop ocr-v6
docker rm ocr-v6
```

NPU 容器使用名称 `ocr-v6-npu`。这些操作只针对新服务，不涉及原有 OCR 容器。

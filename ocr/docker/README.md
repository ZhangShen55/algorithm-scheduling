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

## CPU/NVIDIA GPU 镜像

`docker/Dockerfile` 提供两种构建模式：

- 默认普通模式：保留 Python 源码，便于开发、排障和回滚；
- Cython 模式：显式传入 `--build-arg cython=yes`，核心功能模块编译为 Linux 原生扩展，最终镜像不保留对应 `.py` 源码。

Cython 只提高源码阅读和逆向门槛，不是密码学加密，也不代表产物不可逆向。源码仓库仍是开发、测试和代码审查的唯一来源。

普通 x86_64 Linux 镜像：

```bash
docker build \
  --platform linux/amd64 \
  -f docker/Dockerfile \
  -t jy-ocr-v6-service:3.0-source \
  .
```

Cython 编译保护镜像：

```bash
docker build \
  --platform linux/amd64 \
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

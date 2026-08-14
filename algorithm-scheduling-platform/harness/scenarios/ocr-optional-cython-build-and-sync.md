# 场景：OCR 可选 Cython 构建与双项目同步

## 目标与边界

验证 OCR 服务在同一份 CPU/NVIDIA GPU Dockerfile 中支持普通源码镜像和显式
`cython=yes` 的编译保护镜像，并确认源项目改动以允许清单同步到算法功能调度 OCR 副本。
Cython 只提高源码阅读和逆向门槛，不是密码学加密。本场景不修改、也不验证 NPU Dockerfile。

当前证据来自 Apple Silicon MacBook、`ocr-v6` Conda 环境和 Docker Desktop 的
`linux/amd64` 模拟运行。没有真实 NVIDIA GPU 证据，因此结论只能为“部分符合”。

## 输入与同步基线

- 源项目：`/Users/zhangshen/Documents/workspace/jy-algorithm-app-ocr-v6-service`，
  `main@ffa85e757fa2446fb925747331aecfe9e779cf77`，实施时保留已有未提交改动。
- 目标仓库：`/Users/zhangshen/Documents/workspace/算法功能调度`，
  `codex/milestone-2b-three-gpu-deployment@701afa9f9973b6b062bfa90a66ce7fa101b5f428`。
- CPU 配置：两个项目各自的 `config.toml.example`，只读挂载到 `/app/config.toml`。
- 真实 OCR 输入：`ocr/tests/fixtures/ocr-test.jpg`。
- 模型摘要：源、目标 `models/manifest.sha256` 的 SHA-256 均为
  `818231294db3ca1d430660640fd60cf9f29f1d7decf6f1affb5466bc03365a27`，模型未复制。
- 同步排除：`.git`、`.codex`、`openspec/`、正式 `config.toml`、日志、缓存、字节码、
  临时文件、模型目录和 NPU Dockerfile。

## 共享文件与有意差异

共享能力包括 `docker/build_cython.py`、Cython 构建测试、严格构建参数、最终镜像清理规则、
模型摘要校验和中文 Docker 文档。目标项目通过语义合并保留以下平台专属能力：

- `app.main:app` 导出和 operator registry runtime 安装；
- `REQUIRE_GPU` 门禁及 `cuda:<index>` 校验；
- 固定 registry wheel、`docker/entrypoint.sh` 和单 Uvicorn worker；
- 平台 requirements、wheel、`AGENTS.md` 和测试契约。

同步后 `app/` 的剩余差异仅为上述 operator runtime、`app.main:app` 和 `REQUIRE_GPU` 集成。
源项目规划文件、目标正式配置、模型、平台 wheel 和本地生成物不要求逐字一致。

## 本机正例

从两个项目根目录分别执行：

```bash
conda run -n ocr-v6 python -m compileall -q app
conda run -n ocr-v6 python -m pytest -q tests
```

源项目完整测试为 `160 passed`；目标项目编译、`from app.main import app` 和完整测试通过，
结果为 `164 passed`。目标镜像构建命令：

```bash
docker build --platform linux/amd64 \
  -f docker/Dockerfile \
  -t algorithm-scheduling-ocr:cython-check-source .

docker build --platform linux/amd64 \
  --build-arg cython=yes \
  -f docker/Dockerfile \
  -t algorithm-scheduling-ocr:cython-check .
```

普通镜像大小为 `12803031907` bytes，Cython 镜像大小为 `12805680417` bytes。Cython
镜像包含 16 个原生扩展，不含核心 `.py`、C/C++/目标文件、Cython、gcc、构建目录、依赖清单
或 `/app/config.toml`；registry wheel 安装结果和平台 entrypoint 保留。两种镜像的 12 个模型文件
均通过 `manifest.sha256` 校验。

使用同一份 CPU 配置分别挂载运行后，两个镜像的 `/ocr/getVersion` 和真实
`/ocr/prediction` 均通过。请求 `enable_formula=true` 且服务端 `[formula].enabled=false` 时，
两者都返回原有五个顶层字段，`formula_results[0].status` 为 `disabled`，OCR 结果逐字段一致。

## 失败反例

以下构建必须失败并包含 `cython must be "yes" or "no"`：

```bash
docker build --platform linux/amd64 \
  --build-arg cython=true \
  -f docker/Dockerfile \
  -t algorithm-scheduling-ocr:invalid-check .
```

以下两个容器必须以非零状态退出并报告 `配置文件不存在：/app/config.toml`：

```bash
docker run --rm --platform linux/amd64 algorithm-scheduling-ocr:cython-check-source
docker run --rm --platform linux/amd64 algorithm-scheduling-ocr:cython-check
```

最终镜像内不得存在构建时临时配置：

```bash
docker run --rm --platform linux/amd64 --entrypoint sh \
  algorithm-scheduling-ocr:cython-check -c '
    test ! -e /app/config.toml &&
    test ! -e /app/.build &&
    ! command -v gcc &&
    ! python -m pip show Cython
  '
```

## 真实 NVIDIA GPU 待验收

在 x86_64 NVIDIA 主机准备宿主机 `config.toml`，设置 `device = "cuda:0"`，按需打开
`[formula].enabled`，然后对普通和 Cython 镜像分别执行：

```bash
docker run -d --name ocr-v6-cython-gpu-check \
  --gpus all \
  -e REQUIRE_GPU=true \
  -p 8866:8866 \
  -v "$(pwd)/config.toml:/app/config.toml:ro" \
  --log-driver json-file \
  --log-opt max-size=100m \
  --log-opt max-file=3 \
  algorithm-scheduling-ocr:cython-check
```

验收必须记录容器内 `nvidia-smi`、应用显存占用、普通 OCR、公式开启路径、容器重启后再次推理，
并用同一配置复验普通镜像。上述证据完成前，不得把本场景或 `DEC-023` 改为“符合”。

## 证据结论

- 静态、单元与契约测试：符合。
- MacBook CPU / Docker `linux/amd64` 构建与真实 OCR：符合。
- 双项目允许清单同步及平台专属能力保留：符合。
- 真实 NVIDIA GPU、显存、公式路径和重启：待验证。
- 综合结论：部分符合。

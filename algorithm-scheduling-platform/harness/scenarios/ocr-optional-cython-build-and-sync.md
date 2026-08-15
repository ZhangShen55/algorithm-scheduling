# 场景：OCR 可选 Cython 构建与双项目同步

## 目标与边界

验证 OCR 服务在同一份 CPU/NVIDIA GPU Dockerfile 中支持普通源码镜像和显式
`cython=yes` 的编译保护镜像，并确认源项目改动以允许清单同步到算法功能调度 OCR 副本。
Cython 只提高源码阅读和逆向门槛，不是密码学加密。本场景不修改、也不验证 NPU Dockerfile。

证据包括 Apple Silicon MacBook、`ocr-v6` Conda 环境、Docker Desktop 的 `linux/amd64`
构建，以及 `192.168.29.11` 上的真实 x86_64/NVIDIA GPU 运行。普通与 Cython、源与目标
四个最终镜像均完成普通 OCR、公式识别、显存记录和容器重启复验。

## 输入与同步基线

- 源项目：`/Users/zhangshen/Documents/workspace/jy-algorithm-app-ocr-v6-service`，
  最终修复提交为 `main@797968c9eca8e51f5d52d62b94c38e8c517e30ed`，实施时保留已有未提交改动。
- 目标仓库：`/Users/zhangshen/Documents/workspace/算法功能调度`，
  最终 OCR 修复提交为
  `codex/milestone-2b-three-gpu-deployment@a5106d026b1aa58ed33f9125a0cb67b53e5e25c4`。
- CPU 配置：两个项目各自的 `config.toml.example`，只读挂载到 `/app/config.toml`。
- GPU 配置：同一份宿主机配置设置 `device = "cuda:0"` 和 `[formula].enabled = true`，
  SHA-256 为 `debd64eacc36a9621633046a72715c6f8a8bba1b603c5e0ff647a370e51af02a`。
- 真实 OCR 输入：`ocr/tests/fixtures/ocr-test.jpg`。
- 真实公式输入：`ocr/tests/fixtures/formula-document.png`。
- 模型摘要：源、目标 `models/manifest.sha256` 的 SHA-256 均为
  `818231294db3ca1d430660640fd60cf9f29f1d7decf6f1affb5466bc03365a27`；项目同步未复制模型，
  GPU 隔离验收只传输一份已校验模型并由两个构建上下文复用。
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

源项目完整测试为 `161 passed`；目标项目编译、`from app.main import app` 和完整测试通过，
结果为 `165 passed`。目标镜像构建命令：

```bash
docker build --platform linux/amd64 \
  -f docker/Dockerfile \
  -t algorithm-scheduling-ocr:cython-check-source .

docker build --platform linux/amd64 \
  --build-arg cython=yes \
  -f docker/Dockerfile \
  -t algorithm-scheduling-ocr:cython-check .
```

最终源普通/Cython 镜像 ID 分别为 `cb3ede66`、`c23d9f85`，大小分别为
`12803022141`、`12805661912` bytes；最终目标普通/Cython 镜像 ID 分别为
`de68f904`、`f7df45a3`，大小分别为 `12803034012`、`12805682418` bytes。Cython
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

## 真实 NVIDIA GPU 验收

验收主机为 `192.168.29.11`，架构 `x86_64`，Docker `26.1.4`，NVIDIA 驱动
`570.172.08`。主机包含两张 RTX 4090 D 和一张 RTX 3090。四个最终镜像均使用以下约束
逐个运行，`CUDA_VISIBLE_DEVICES=2` 将物理 GPU 2 映射为容器逻辑 `cuda:0`：

```bash
docker run -d --name ocr-v6-cython-gpu-check \
  --gpus all \
  -e CUDA_VISIBLE_DEVICES=2 \
  -e REQUIRE_GPU=true \
  -p 18866:8866 \
  -v "$(pwd)/config.toml:/app/config.toml:ro" \
  --log-driver json-file \
  --log-opt max-size=100m \
  --log-opt max-file=3 \
  algorithm-scheduling-ocr:cython-fixed
```

首次真机启动暴露出镜像内 `/usr/local/cuda-11.8/compat/libcuda.so.520.61.05` 先于宿主机
`libcuda.so.570.172.08` 加载，CUDA 返回错误 803。源提交 `797968c` 和目标提交 `a5106d0`
删除该错误优先级，并把 `/usr/lib/x86_64-linux-gnu` 放在 `LD_LIBRARY_PATH` 首位。修复后的
四个 Dockerfile 产物重新构建、传输并完成以下验收：

- 容器内 Paddle 仅看到一个逻辑 GPU；四组进程都运行在物理 GPU 2，GPU 0/1 保持 `3 MiB`。
- 源普通、源 Cython、目标普通、目标 Cython的进程显存分别为 `2414`、`2424`、`2414`、
  `2418 MiB`，主机统计显存分别为 `2423`、`2433`、`2423`、`2427 MiB`。
- 普通 OCR 均识别 2 条结果，响应 SHA-256 均为
  `23810a5eca86757c7232bafc0a395533f5d035c2f31eb6213464f3b4dae30c1e`。
- 公式路径均识别 28 个公式，响应 SHA-256 均为
  `ab7d92d0801f530d04955f97f28b43f4115f3108e77f9396f99df00c7f3a0780`。
- 四个容器重启后版本接口和真实 OCR 再次通过，重启前后 OCR 响应逐字一致。
- 每组结束后精确删除验收容器；最终无本次活跃容器，三张 GPU 均恢复到 `3 MiB`。

## AMD64 离线交付与 RTX 3090 压测（2026-08-15）

本次在源项目以 `--platform linux/amd64 --build-arg cython=yes` 生成唯一交付标签
`ocr:v6_amd`，再保存为 `ocr_v6_amd.tar`。本机 Docker manifest-list ID 为
`sha256:ca25c2aa073495fe4be377f433475c1c5e542d7f63897a35aba9cf5a4d8d3203`；Linux
加载后的 AMD64 镜像 ID 为
`sha256:bba69f2ab3f9521c3d5dde8d3f3803a52f673925d3204552738347c8ff3d5abe`。
tar 大小为 `12,806,246,400` 字节，SHA-256 为
`8201d9234eeac95cc993f76d74890f0dbbce4910a018e2db6ba0472790822cd9`，本机与服务器
复校验一致。

最终镜像包含 16 个核心原生扩展和四套模型的 12 个受校验文件；不包含核心 `.py`、
C/C++/目标文件、构建目录、Cython、gcc、g++、make 或 `/app/config.toml`。正式配置通过
只读挂载提供。服务器正式容器 `ocr-v6-amd` 只映射物理 GPU 2（RTX 3090），容器内使用
逻辑 `cuda:0`；固定 `enable_hpi = false`、`max_concurrency = 1`，客户端并发由单引擎
排队处理。

压测从服务器本机发起，OCR batch 为 `1/4/8/16`，客户端并发为 `1/2/4/8/16`；每组
10 次预热、100 次计量，共 2,000 个计量请求。各 batch 按并发 `1/2/4/8/16` 的 QPS
矩阵如下：

| Batch | 5 组 QPS |
| ---: | --- |
| 1 | `11.107 / 12.687 / 12.840 / 12.959 / 13.007` |
| 4 | `12.669 / 14.062 / 14.075 / 14.173 / 13.926` |
| 8 | `12.449 / 13.834 / 13.808 / 13.839 / 13.908` |
| 16 | `12.580 / 14.107 / 13.857 / 14.030 / 13.833` |

20 组全部 100% 成功、0 个 HTTP 5xx，内容摘要均为 `8c763fa078097ca0`，GPU 总显存
约 `1043 MiB`。按 95% 峰值规则选择最小合格参数，最终推荐
`recognition_batch_size = 4` 和客户端并发 `2`；服务端 `max_concurrency` 仍为 `1`。
推荐组合独立复验 100/100 成功，`13.468 QPS`，P95 `152.716 ms`。

公式路径独立启用后识别出 28 个公式，单请求约 `9.806 s`，GPU 显存约 `2191 MiB`。
容器重启前后版本接口和真实 OCR 均通过，响应摘要一致。隔离临时容器还真实复现了配置
缺失、GPU 不可用、模型目录缺失和端口占用日志；未影响正式容器。

清理只按明确标签执行：本机保留 `ocr:v6_amd`、tar 和 `jy-ocr-service:local`；服务器保留
`ocr:v6_amd`、tar、正式容器和全部 `algorithm*` 镜像，未执行 `docker system prune`。
源/目标 `models/manifest.sha256` 摘要均为
`818231294db3ca1d430660640fd60cf9f29f1d7decf6f1affb5466bc03365a27`，同步没有读取、
复制或删除模型及目标正式配置。目标平台入口、operator runtime、`REQUIRE_GPU`、registry
wheel、BuildKit `ocr_model_manifest` secret 和 NPU Dockerfile 均保留。

完整环境、20 组延迟分位数、显存、公式、重启和失败日志见
`ocr/docs/ocr-v6-rtx3090-benchmark.md`。该结论达到静态/单元/契约、完整 AMD64 镜像构建、
真实 RTX 3090 推理、固定矩阵压测和容器重启层级；单图片夹具不代表所有生产图片分布。

## 证据结论

- 静态、单元与契约测试：符合。
- MacBook CPU / Docker `linux/amd64` 构建与真实 OCR：符合。
- 双项目允许清单同步及平台专属能力保留：符合。
- 真实 NVIDIA GPU、显存、公式路径和重启：符合。
- `ocr:v6_amd` 离线 tar、RTX 3090 固定矩阵和推荐配置：符合。
- 综合结论：符合。

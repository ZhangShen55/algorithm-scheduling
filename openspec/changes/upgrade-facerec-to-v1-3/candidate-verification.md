# FaceRec v1.3 候选门禁汇总

## 结论

- 验证日期：2026-09-18（Asia/Shanghai）
- 上游基线：`git@github.com:ZhangShen55/FaceRec.git` 的 `v1.3`，提交 `980e81b7a0bf9bffc07cd5c338a3f91eef1a019d`
- 候选分支：`codex/upgrade-facerec-to-v1-3-candidate`
- 候选提交：`9a455a1707d4cab1b19179c5a31142b84872f83a`
- 远端分支核对：`git ls-remote` 返回上述完整候选提交
- 结论：本地代码、真实 CPU 推理、本机 AMD64 镜像，以及 `192.168.29.11` 的三实例严格 GPU、真实租约与 Online Gateway 门禁均通过；正式目录和旧远端资产可进入受控替换阶段。

## 本地源码门禁

执行环境为 macOS、Conda `facerecapi`、Python 3.10，项目目录为 `facerec_v1.3/`。

```bash
conda run -n facerecapi python -m compileall -q app
conda run -n facerecapi python -c 'from app.main import app'
conda run -n facerecapi python -m pip check
conda run -n facerecapi python -m pytest -q tests
FACEREC_RUN_REAL_INFERENCE=1 conda run -n facerecapi python -m pytest -q tests/test_real_inference.py
```

结果摘要：

- `compileall`、应用导入、关键依赖导入和 `pip check` 均通过。
- 全量测试为 `81 passed, 2 skipped, 1 warning in 39.87s`；两个 skip 为需要显式外部环境的测试，不是失败。
- 真实单人录入/识别、512 维 embedding、双人脸检测与匹配、`save_person_photo=false` 不落图均通过。
- Uvicorn、health/readiness、OpenAPI、优雅关闭、MongoDB 人员管理、Redis 依赖边界和严格 GPU 负向测试均通过。
- macOS 导入时存在系统 OpenCV 双动态库告警，但退出码为 0；指定服务器 Linux 镜像没有该告警。

## 本机 Docker 门禁

执行环境为 Docker Desktop `29.5.3`、Linux engine `arm64`，目标平台为 `linux/amd64`。构建保留已有 layer cache，构建上下文约 `702.3 MB`，未使用 `--no-cache`，未执行任何 prune。

```bash
docker build --platform linux/amd64 \
  --label org.opencontainers.image.revision=9a455a1707d4cab1b19179c5a31142b84872f83a \
  --label org.opencontainers.image.source=git@github.com:ZhangShen55/FaceRec.git \
  --build-arg FASTDEPLOY_FIND_LINKS=http://host.docker.internal:18765/ \
  --build-arg FASTDEPLOY_TRUSTED_HOST=host.docker.internal \
  -t algorithm-facerec:local-9a455a1 -f docker/Dockerfile .
docker image inspect algorithm-facerec:local-9a455a1
docker run --rm --platform linux/amd64 --entrypoint /bin/sh \
  algorithm-facerec:local-9a455a1 -c '<pip check 与文件系统允许清单检查>'
```

结果摘要：

- 镜像 ID：`sha256:374d59e0c0eaf06bd6ba03a883cbe11afb01b628ea5e5b91eb5b95fce683d13d`
- 架构与大小：`linux/amd64`，`5,150,576,771` 字节。
- 工作目录为 `/app`，入口为 `/usr/local/bin/facerec-entrypoint`，revision label 与候选提交一致。
- 镜像内 `pip check` 返回 `No broken requirements found.`，七个 `.onnx`/`.dat` 模型均存在且非空。
- 镜像内不存在 `.git`、测试、文档、部署 tar、`.msc`、日志内容、媒体内容、PEM/key 文件；`logs/`、`media/person_photos/` 和 `media/detections/` 空目录存在。

首次本机构建在 Apple Silicon 的 QEMU 环境中从源码链接 `dlib==20.0.0` 时出现 GNU make jobserver 文件描述符错误。同一 Dockerfile 已在指定服务器原生 AMD64 成功构建；随后由该原生环境生成同版本 AMD64 wheel（SHA-256 `e77c30787313f4e3a3a82909546f0a75785a0570126454bfd8dee2baaa0dfd07`），通过既有受信缓存隧道重建通过。该修正未改变产品依赖版本。为绕开本机失效的 Docker credential helper，构建使用不含凭据的临时 Docker 配置；用户 Docker 凭据未读取或修改。

## 指定服务器候选门禁

执行主机为 `192.168.29.11`。登录凭据不进入本文件、普通日志或新增配置。候选构建复用并保留 BuildKit cache，未使用 `--no-cache`，未执行 builder、buildx 或 system prune。

- 候选镜像：`algorithm-facerec:candidate-9a455a1`
- 镜像 ID：`sha256:5d95e67096b1811cabba6a2393a96420e3d4b3b94cc63f53b0d5c71429cbf355`
- 镜像大小：`11,500,916,999` 字节
- revision：`9a455a1707d4cab1b19179c5a31142b84872f83a`
- BuildKit cache：构建前后均约 `96.72 GB`
- 镜像内检查：`pip check` 通过，七个必需模型均非空

隔离候选实例：

| 实例 | 监听地址 | GPU | 结果 |
| --- | --- | --- | --- |
| `facerec-candidate-gpu0` | `127.0.0.1:18013` | 0 | healthy、ready、真实识别通过 |
| `facerec-candidate-gpu1` | `127.0.0.1:28013` | 1 | healthy、ready、真实识别通过 |
| `facerec-candidate-gpu2` | `127.0.0.1:38013` | 2 | healthy、ready、真实识别通过 |

三实例均满足：

- `require_gpu=true`，ArcFace 使用 FastDeploy GPU，InsightFace 五个 session 的首选 provider 均为 `CUDAExecutionProvider`。
- 对应 GPU 上同时存在候选主进程和 detector worker，无 CPU 回退；真实识别延迟约 `85-124 ms`。
- `model_ready=true`、MongoDB `up`、声明容量 `128`。
- gpu0 新增、gpu1 更新、gpu2 删除后，三实例通过共享 MongoDB 立即观察到一致事实；命中相似度为 `100.00%`，删除后均不再命中。
- `photo_path` 为空，`media/person_photos/` 无新增文件；未执行算子侧 Redis cache 失效。
- 日志均为 JSON Lines，扫描未发现 Base64、人脸图片、完整请求/响应、人员测试数据、凭据或 embedding 内容。

平台验证使用真实 Control Service 与 Online Gateway 程序的隔离候选实例，Redis key 前缀与正式平台隔离，只允许三个候选 FaceRec 注册。真实租约成功选中候选实例并释放；隔离 Online Gateway `/ready` 通过，`/api/online/face/recognize` 返回平台外层 `code=0`，FaceRec 内层 `status_code=200`、`has_face=true`。gpu2 先进入 `DRAINING` 并对新业务请求返回 `503`，随后注销和停止；重启后重新注册为 `ONLINE`。

正式 Control Service 初次拒绝候选实例名并返回 `403`，原因是其可信 origin 映射只允许正式实例名，证明保护边界生效；未修改或重启正式 Control Service。人工验证初次把 `/persons/delete` 误用为其他方法，按既有 OpenAPI 改为 `DELETE` 后通过，产品路由和实现无需修复。

## 替换前保护状态

- 父仓库正式 `facerec/` 在候选门禁期间保持零差异。
- 正式旧 FaceRec 三实例和镜像保持运行，尚未删除。
- 候选嵌套仓库跟踪的 `.msc` 不在 Docker 镜像中，最终允许清单迁移必须排除它并补充忽略规则。
- 最终迁移必须保留父仓库受管的 `facerec/tests/data/常泽宇.png`，以保证 clean clone 可执行真实推理。
- `.gitignore`、ASR Online、Text Analysis 和另一 OpenSpec change 的用户修改不属于本 change，后续提交不得包含或改写。

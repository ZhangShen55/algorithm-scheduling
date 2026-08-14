# 里程碑 2B 三卡部署验证场景

## 目标与证据边界

本场景是里程碑 2B 的部署验证入口，目标是验证 x86_64、三张 NVIDIA GPU
服务器上的八类算子、24 个容器实例、四个平台服务和四类基础设施。执行顺序
固定为：

```text
preflight -> snapshot/pause -> infrastructure -> model staging/verify
-> build 8 images -> compose gpu0/gpu1/gpu2/cpu
-> GPU UUID/PID/cgroup -> 24 instance registration
-> 8 operator smoke -> negative/load/recovery
-> stop project -> restore existing containers -> render report
```

测试状态只能是 `通过`、`失败` 或 `未执行及原因`。Task 7B-9 的本地代码门禁
通过不表示真实部署通过：当前尚未取得真实服务器、三卡驱动、六根模型资产、24
实例注册、真实媒体推理或完整离线/在线泳道证据。ScreenDet 是在线网关调用的
图像质量算子，不属于离线课程 DAG。

## 服务器前提和安全边界

- 目标：`root@192.168.29.11:22`；代码目录：`/root/workspace/algorithm-scheduling`。
- 必须为 `x86_64`，Docker、Compose v2、NVIDIA Container Runtime 可用，且容器
  `nvidia/cuda` 运行时能看到恰好三张 GPU。
- `/data/course` 和 `/data/result` 必须是实际目录并可由执行身份完成同步写入；
  `/data/result` 为持久结果目录，禁止部署清理流程删除。
- PostgreSQL、Redis、Kafka、MongoDB 必须先健康；容器内使用 Compose 网络地址，
  宿主机进程使用 `127.0.0.1` 地址，不能混用 Kafka listener。
- 登录密码、Deploy Key、模型解密密钥、课程视频、人脸原图和外部 fixture 只通过
  安全外部通道提供，禁止出现在 Git、Markdown、JSON 报告、进程参数和 shell 历史。
- 允许暂停已有业务容器，但必须先快照，使用同一 canonical ledger 恢复；禁止
  `docker system prune`、`docker compose down -v` 和删除 `/data/result`。

## 发布变量和报告目录

以下命令在 `algorithm-scheduling-platform` 目录执行。`EXPECTED_GIT_SHA` 必须是
工作树当前 HEAD 的完整 40 位 SHA；模型源必须位于 Git 工作树外、目录权限 `0700`。

```bash
RELEASE_TAG=v1.0_260812
EXPECTED_GIT_SHA="$(git -C .. rev-parse HEAD)"
MODEL_ASSET_SOURCE=/root/workspace/.algorithm-scheduling-assets/v1.0_260812
RESTRICTED_REPORT_ROOT=/root/workspace/.algorithm-scheduling-restricted-reports
REPORT_ROOT="$PWD/deploy/reports"
RELEASE_ROOT="$REPORT_ROOT/milestone-2b/releases/$RELEASE_TAG/$EXPECTED_GIT_SHA"
```

初始化外部模型清单和报告目录：

```bash
deploy/scripts/generate-model-asset-manifest \
  --source "$MODEL_ASSET_SOURCE" --workspace "$PWD/.."
deploy/scripts/prepare-report-directory \
  --release-tag "$RELEASE_TAG" --git-sha "$EXPECTED_GIT_SHA" \
  --reports-root "$REPORT_ROOT" --restricted-root "$RESTRICTED_REPORT_ROOT" \
  --external-manifest "$MODEL_ASSET_SOURCE/model-assets.manifest.json"
```

`model-assets.manifest.json` 只归档到 Git 外的受限目录；报告只记录模型根、文件
数和总字节数，不记录逐文件哈希或密钥元数据。ASR Offline、ASR Online、OCR、VBas、
FaceRec、ScreenDet 六个模型根必须由外部 manifest 冻结；PPT Slice 和 Text Analysis
没有本地模型根。

## 阶段 1：服务器预检、快照和暂停

```bash
EXPECTED_GIT_SHA="$EXPECTED_GIT_SHA" deploy/scripts/preflight \
  >"$RELEASE_ROOT/preflight/preflight.log" 2>&1

SNAPSHOT="$RELEASE_ROOT/container-maintenance/existing-containers.jsonl"
deploy/scripts/snapshot-existing-containers "$SNAPSHOT"
```

只暂停经过确认的业务容器；不要使用空选择器或按宽泛名称匹配：

```bash
deploy/scripts/pause-existing-containers "$SNAPSHOT" <container-id-or-exact-name>...
```

暂停账本固定为 `${SNAPSHOT}.paused.jsonl`，必须保留到恢复完成。预检失败时停止
后续阶段，并将原因写入 `preflight` 报告，不得强行继续。

## 阶段 2：基础设施、模型资产和八镜像

```bash
docker compose -f deploy/docker-compose.infrastructure.yml up -d
docker compose -f deploy/docker-compose.infrastructure.yml ps

deploy/scripts/stage-model-assets \
  --source "$MODEL_ASSET_SOURCE" --workspace "$PWD/.."
deploy/scripts/verify-model-assets \
  --source "$MODEL_ASSET_SOURCE" --workspace "$PWD/.."

EXPECTED_GIT_SHA="$EXPECTED_GIT_SHA" MODEL_ASSET_SOURCE="$MODEL_ASSET_SOURCE" \
  deploy/scripts/build-images "$RELEASE_TAG"
```

构建入口先重建并发布 `algorithm-operator-registry-client==0.1.0` wheel，再逐个
构建 `deploy/operator-images.tsv` 中的八个镜像，并检查磁盘、镜像 tag 和
`org.opencontainers.image.revision`。上下文门禁和模型校验任何一步失败都必须停在
本阶段。

构建期间允许 pip/Conda 在镜像内在线下载 Wheel。下载源可达且网络字节、Docker
缓存或文件系统写入仍持续增长时，即使 BuildKit 日志暂时静默也必须继续等待。只有
明确的 HTTP 403/404、连接失败，或持续无字节/无磁盘进展，才可中止并记录证据。
构建期间由独立监控任务分别跟踪阶段/下载、磁盘/内存/进程和八镜像 revision 矩阵。
“持续无进展”默认要求至少 15 分钟内每 60 秒采样一次，且网络接收字节、Docker
缓存/镜像大小、相关文件系统写入和下载进程四类证据均无增长；任一证据有进展就重置
观察窗口。

### 本次远端执行结果（2026-08-12）

服务器预检、模型资产传输、staging 和六个模型根逐文件校验已经通过。模型源中发现的
两个 staging 污染文件（`vbas/models/.DS_Store`、`ocr/models/manifest.sha256`）已在
Git 工作树外的受控 staging 源中精确移除，原始算子目录未修改。
这是当时的执行事实。`manifest.sha256` 在外部模型根中仍属于平台污染，只有经权威总清单
投影后位于最终镜像内的运行时派生副本合法。现行合同不把该文件放回模型源或 Git，
而是在每次发布构建时从外部总清单投影
OCR 子集，以 BuildKit secret 校验镜像内精确文件集，再生成镜像内运行时派生清单。

八镜像构建在第一个 ASR Offline 镜像解析
`nvcr.io/nvidia/cuda:12.1.1-cudnn8-runtime-centos7` 时被 registry 大层 TLS handshake
超时阻塞；随后两次有界 `docker pull` 重试仍卡在同一层，未生成完整基础镜像。根据门禁，
后续平台、算子和真实推理阶段保持“未执行及原因”，不能把模型校验 PASS 或本地测试 PASS
解释为部署完成。恢复 registry 传输或提供同一镜像 digest 的内部缓存后，从本阶段的基础
镜像预拉取继续。

### ASR 真机验证与构建续接（2026-08-14）

用户后续准备好 CentOS 7 CUDA 基础镜像，并确认 ASR 采用 Python 3.11、
Torch/Torchaudio 2.6.0。ASR Offline 镜像已在目标服务器构建，并通过 RTX 4090 D
真实推理：容器内 `torch.cuda.is_available()` 为真，`/v1.1.8/seacraft_asr` 返回
HTTP 200 和非空文本，推理期间 `nvidia-smi` 可见 `asr_offline` 进程。该结果只证明
ASR Offline 单镜像和单次真实 GPU 推理，不代表八镜像或里程碑 2B 整体通过。

继续构建时，ASR Online 在下载官方 Miniconda 安装器处长时间停顿；同机测速显示官方源
约 0.60 MB/s、清华镜像约 5.43 MB/s。两个 ASR Dockerfile 因此将 Miniconda 基地址
参数化并默认指向清华镜像。取得包含此变更的新完整 Git SHA 后，应重新设置
`EXPECTED_GIT_SHA` 并从本阶段的 `build-images` 命令继续。历史 CUDA 基础镜像下载阻塞
记录必须保留，不能用本次结果覆盖。

后续真实 GET 复核发现：清华入口对 curl HEAD 可返回重定向，但对 CentOS 7/wget GET
返回 HTTP 403；其最终重定向目标南京大学镜像对同一容器 GET 成功，4 秒取得完整
141613749 字节安装器。因此“默认清华镜像”的决定已被覆盖，当前默认值改为
`https://mirror.nju.edu.cn/anaconda/miniconda`，build arg 覆盖方式不变。必须使用包含该
修正的新完整 Git SHA 继续构建。

OCR 可选 Cython 构建随后暴露出跨提交门禁冲突：其构建期导入校验需要无敏感值的
`config.toml.example`，而旧门禁禁止所有 `!config*` 重包含。当前门禁只对 OCR 精确允许
`!config.toml.example`，仍拒绝正式 `config.toml` 和其他配置重包含；镜像构建测试与真实
八上下文门禁均已通过。继续构建时必须使用同时包含 Miniconda 源修正和本门禁修复的新 SHA。

使用 `c36dbc45c4c3a7e721785eb4a5cd8e12757c8cd4` 续接后，上述两项阻塞均已越过；
ASR Offline 成功创建 Python 3.11.15 Conda 环境，并完整下载 766.7 MB 的 Torch 2.6.0
wheel。但 PyTorch 随后仍需下载约十余个 CUDA 12.4 拆分 wheel 和 Triton，当前链路速率
降至约 0.2-0.6 MB/s。当时依据旧的有界下载规则，构建在 CUDA CUPTI wheel 阶段主动中止，
后续七镜像未开始。该中止是历史事实，但判定规则已被本节新策略取代：0.2-0.6 MB/s
仍是明确进展，不再构成中止理由。不得把单个 ASR 真机推理通过外推为八镜像通过。

## 阶段 3：平台和逐卡算子拓扑

```bash
docker compose -f deploy/docker-compose.platform.yml up -d --build
docker compose -f deploy/docker-compose.platform.yml ps

docker compose -f deploy/docker-compose.operators.yml --profile gpu0 up -d
docker compose -f deploy/docker-compose.operators.yml --profile gpu1 up -d
docker compose -f deploy/docker-compose.operators.yml --profile gpu2 up -d
docker compose -f deploy/docker-compose.operators.yml --profile cpu up -d
docker compose -f deploy/docker-compose.operators.yml ps
```

权威 Compose 必须保持 24 个实例：18 个 GPU 实例（六类 GPU 算子 × 三卡）和
6 个 CPU 实例（PPT Slice × 三、Text Analysis × 三）。每个容器一个 Uvicorn
worker；同一实例 ID 不得被两个容器复用。容器配置只读挂载 `deploy/config/operators`，
模型和业务结果分别遵守 `/data/course`、`/data/result` 边界。

## 阶段 4：GPU 真实性证据

每个 GPU 容器必须在真实推理触发器存活期间采样。触发器是 JSON argv 数组，不经过
shell；输出只能写入当前 release 的 `gpu-instances/`，不能覆盖已有证据：

```bash
deploy/scripts/verify-gpu-instance \
  --container asr-offline-gpu0 --physical-gpu 0 --process-name asr_offline \
  --trigger-file /tmp/asr-offline-gpu0-trigger.json \
  --output "$RELEASE_ROOT/gpu-instances/asr-offline-gpu0.json"
```

对 18 个 GPU 实例逐一替换容器、物理 GPU 和算子名。通过条件同时包括：容器只见
一张目标卡、UUID 与宿主一致、`cuda:0`/框架设备证据、`nvidia-smi` 中目标算子
进程名、宿主 CUDA PID、`docker top` 映射、完整 64 位 cgroup ID 和 NSpid。停止容器
后用 `--assert-stopped --evidence <prior-json>` 将残留检查写入 `recovery/`。

## 阶段 5：注册、Smoke 和报告

24 实例必须经历“注册 -> 首次心跳 -> ONLINE -> model_ready=true -> capability/
GPU 标签匹配”；只有注册响应不能通过：

```bash
deploy/scripts/verify-operator-registration \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --git-sha "$EXPECTED_GIT_SHA" \
  --reports-root "$REPORT_ROOT"
```

八类 Smoke 使用 `deploy/operator-smoke-cases.json`、`operator-smoke-fixtures.json`
和外部 fixture manifest。ASR Offline 调用 v1.1.8；ASR Online 使用 WebSocket；
OCR/VBas/FaceRec/ScreenDet 使用单图；PPT 必须使用冻结的本地 `video_path`，等待一次
终态 manifest 回调；Text Analysis 调用 `extract_keywords` 与 `course_overviews`。
缺失的 ASR/VBas/PPT fixture 必须写成“未执行及原因”，不能改用随意媒体冒充基准：

```bash
deploy/scripts/run-operator-smoke \
  --release-tag "$RELEASE_TAG" --git-sha "$EXPECTED_GIT_SHA" \
  --reports-root "$REPORT_ROOT" \
  --fixture-manifest /root/workspace/.algorithm-scheduling-fixtures/v1.0_260812/manifest.json \
  --external-fixture-root /root/workspace/.algorithm-scheduling-fixtures/v1.0_260812 \
  --fixture-target-root /data/course/_harness/fixtures \
  --result-root /data/result/_harness \
  --callback-advertise-base-url http://127.0.0.1:19090 \
  --endpoints-json /root/workspace/.algorithm-scheduling-fixtures/v1.0_260812/endpoints.json
```

在线图片 Smoke 只验证网关路由，不进入 Kafka 或离线媒体下载；实时 ASR 验证
WebSocket 会话粘性。该阶段仍是算子直接调用证据，不得通过 Repository 伪造课程节点
完成状态。

## 阶段 6：反例、压力、恢复和报告渲染

至少执行以下类别，并将每条用例写入 `negative/`、`load/` 或 `recovery/`：缺失模型、
manifest hash 漂移、错误 GPU 标签、双可见 GPU、重复 instance_id、注册未心跳、
OFFLINE/DRAINING 路由、容量耗尽、HTTP 429/503、超时、错误输入、PPT 无终态、Kafka
重启、Redis TTL 到期、磁盘不可写、容器停止后 CUDA PID 残留、重复请求和并发超限。
压力报告必须记录并发、队列、成功/失败、p95/p99 和资源峰值；失败或未执行必须保留
原因，不能以 health 代替推理。

停止本项目容器时只使用本项目 Compose，不带 `-v`：

```bash
docker compose -f deploy/docker-compose.operators.yml \
  --profile gpu0 --profile gpu1 --profile gpu2 --profile cpu down
docker compose -f deploy/docker-compose.platform.yml down
docker compose -f deploy/docker-compose.infrastructure.yml down
deploy/scripts/restore-existing-containers "$SNAPSHOT" "${SNAPSHOT}.paused.jsonl"
```

最后渲染当前 release 的 JSON/Markdown 汇总；renderer 要求通过用例有证据文件、
未执行用例有中文原因、所有用例使用同一 release/SHA，并拒绝跨目录或包含敏感 token：

```bash
.venv/bin/python scripts/render_milestone_2b_report.py \
  --input "$RELEASE_ROOT/summary/cases.json" \
  --release-root "$RELEASE_ROOT" \
  --output-json "$RELEASE_ROOT/summary/report.json" \
  --output-markdown "$RELEASE_ROOT/summary/report.md"
```

## 未执行声明

在本台 MacBook 上只能运行 `test_milestone_2b_*`、平台全量测试、服务测试、静态
lint/type/compile、Compose 解析、Shell 语法和严格 OpenSpec 校验。未连接远端，
因此不能声明真实 x86_64/三卡、八镜像构建、24 实例注册、GPU PID、真实模型/课程媒体、
反例/压力/恢复或完整离线/在线泳道通过。

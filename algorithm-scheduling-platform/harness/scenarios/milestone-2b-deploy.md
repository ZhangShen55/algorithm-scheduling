# 里程碑 2B 三卡部署验证场景

## 目标与证据边界

本场景是里程碑 2B 的部署验证入口，目标是验证 x86_64、三张 NVIDIA GPU
服务器上的八类算子、24 个容器实例、四个平台服务和四类基础设施。执行顺序
固定为：

```text
preflight -> snapshot/pause -> infrastructure -> model staging/verify
-> build 8 images + platform images -> runtime attestation
-> compose gpu0/gpu1/gpu2/cpu + profile attestation
-> GPU UUID/PID/cgroup -> restart/ONLINE -> 24 instance registration
-> 8 operator smoke -> negative/load/recovery
-> stop only newly-added operators -> restore ocr-v6-amd -> render report
```

测试状态只能是 `通过`、`失败` 或 `未执行及原因`。Task 7B-9 的本地代码门禁和历史
八镜像构建通过不表示真实部署通过：当前仍未取得最终 SHA 对应的平台/算子运行
attestation、24 实例同时 ONLINE、18 个 GPU 实例活动、真实媒体全量推理或完整离线/
在线泳道证据。ScreenDet 是在线网关调用的
图像质量算子，不属于离线课程 DAG。

## 服务器前提和安全边界

- 目标：`root@192.168.29.11:22`；密码：`kedacom_123`；代码目录：
  `/root/workspace/algorithm-scheduling`。本次部署不使用 `.env`；用户已批准 Git 保存部署
  模板、该登录合同和受控服务默认值。
- 必须为 `x86_64`，Docker、Compose v2、NVIDIA Container Runtime 可用，且容器
  `nvidia/cuda` 运行时能看到恰好三张 GPU。
- `/data/course` 和 `/data/result` 必须是实际目录并可由执行身份完成同步写入；
  `/data/result` 为持久结果目录，禁止部署清理流程删除。
- PostgreSQL、Redis、Kafka、MongoDB 必须先健康；容器内使用 Compose 网络地址，
  宿主机进程使用 `127.0.0.1` 地址，不能混用 Kafka listener。Kafka 固定使用
  `EXTERNAL://:9092`/`INTERNAL://:29092`，分别广播
  `EXTERNAL://127.0.0.1:9092`/`INTERNAL://kafka:29092`。
- 只有 `control-service:18100`、`online-gateway-service:18103` 可绑定全部宿主机地址供
  A/远程可信内网访问。`5432`、`9092`、`6379`、`27017`、`18101`、`18102` 和全部
  24 个算子宿主机端口必须绑定 `127.0.0.1`；容器间服务名和容器端口保持不变。
- 上述服务器密码是经用户批准写入 Markdown/Git 的明确例外。Deploy Key/私钥、模型
  解密密钥、课程视频、人脸原图、大型 fixture 和外部可信模型 manifest 仍只通过安全
  外部通道提供，不得进入 Git、普通 JSON 报告、镜像上下文或命令参数。
- 不允许宽泛暂停已有业务容器。必须先快照，并且只在同一 canonical ledger 中暂停用户
  明确允许的原 `ocr-v6-amd`，验收后恢复；禁止
  `docker system prune`、`docker compose down -v` 和删除 `/data/result`。

## 发布变量和报告目录

以下命令在 `algorithm-scheduling-platform` 目录执行。`EXPECTED_GIT_SHA` 必须是
工作树当前 HEAD 的完整 40 位 SHA；模型源必须位于 Git 工作树外、目录权限 `0700`。
从本节发布变量开始到阶段 6 结束，全部 Bash 代码块必须复制到同一 Bash 会话中按文档
顺序连续执行，不得为每个阶段另开 shell；这样变量、trap、函数和 strict mode 才能持续生效。

```bash
set -euo pipefail

RELEASE_TAG=v1.0_260812
EXPECTED_GIT_SHA="$(git -C .. rev-parse HEAD)"
MODEL_ASSET_SOURCE=/root/workspace/.algorithm-scheduling-assets/v1.0_260812
RESTRICTED_REPORT_ROOT=/root/workspace/.algorithm-scheduling-restricted-reports
REPORT_ROOT="$PWD/deploy/reports"
RELEASE_ROOT="$REPORT_ROOT/milestone-2b/releases/$RELEASE_TAG/$EXPECTED_GIT_SHA"
```

校验外部可信模型清单并初始化报告目录。不得在部署阶段运行 manifest 生成器或覆盖
`$MODEL_ASSET_SOURCE/model-assets.manifest.json`：

```bash
deploy/scripts/verify-model-assets \
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
EXPECTED_GIT_SHA="$EXPECTED_GIT_SHA" deploy/scripts/preflight host \
  >"$RELEASE_ROOT/preflight/preflight.log" 2>&1

SNAPSHOT="$RELEASE_ROOT/container-maintenance/existing-containers.jsonl"
deploy/scripts/snapshot-existing-containers "$SNAPSHOT"
```

确认精确容器身份后，只暂停用户已允许的原 `ocr-v6-amd`；不要使用空选择器或按宽泛
名称匹配：

```bash
docker inspect ocr-v6-amd >"$RELEASE_ROOT/container-maintenance/ocr-v6-amd-before.json"
deploy/scripts/pause-existing-containers "$SNAPSHOT" ocr-v6-amd
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

提交 `8d5e63718bba56225fd0eda0f05935a6a4c9c84c` 的后续真实构建已证明上述 OCR 投影契约：
13 个文件精确校验通过，OCR 镜像成功。流水线随后在第 5 个 FaceRec 镜像中从
`files.pythonhosted.org` 下载 pip Wheel 时触发 `ReadTimeoutError`。新版 FaceRec Dockerfile
为构建工具和业务依赖统一使用可配置 PyPI 源、300 秒超时、10 次重试和 Wheel 优先。
业务依赖步骤另通过可配置 `FASTDEPLOY_FIND_LINKS` 解析标准 PyPI 不提供的
`fastdeploy-gpu-python==1.0.7` CPython 3.10 Linux Wheel。
只能在包含该修复的新完整 SHA 上续接；当前真实证据为 `4/8` 镜像通过，不是八镜像完成。

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

### 八镜像最终构建结果（2026-08-15）

Wheel 和基础镜像允许在 Docker build 内在线下载的策略已取得真实证据。FaceRec 从 Paddle
Wheel 页面完整下载并安装约 1362.1 MB 的
`fastdeploy_gpu_python-1.0.7-cp310-cp310-manylinux1_x86_64.whl`；只要网络、缓存、磁盘
写入或相关进程存在进展，三个独立监控任务均保持等待，没有因下载耗时中断构建。

Text Analysis 首轮因默认 PyPI 的连接重置和 15 秒读取超时失败。增加可覆盖的清华索引、
300 秒超时、10 次重试和 Wheel 优先后，依赖安装通过；随后发现 PyArmor 试用许可证无法
以默认强度处理 `app/models/entities.py`。批准的方案 A 将 PyArmor 固定为 `8.5.12`，其余
57 个源码文件保持默认强度混淆，`entities.py` 单独以 `--obf-code 0` 混淆并安装回同一
产物目录。运行镜像不包含该文件的明文副本，但该文件的函数级保护强度低于其余源码。

提交 `e65dd576b3b53b73a874bb131449ef031423057b` 已在目标 x86_64 服务器完成统一构建。
以下八个 `v1.0_260812` 镜像均存在，逐个 inspect 的
`org.opencontainers.image.revision` 全部精确等于该提交：

- `seacraft-asr-offline`
- `seacraft-asr-online`
- `algorithm-ocr`
- `algorithm-vbas`
- `algorithm-facerec`
- `algorithm-screen-det`
- `algorithm-ppt-slice`
- `algorithm-text-analysis`

统一构建日志终态为 `PASS: eight images built and inspected`。Text Analysis 成品镜像额外完成
导入和启动 Smoke：`app.main:app`、`ModelCard` 均可导入，产物文本中不存在明文
`class ModelCard`，容器启动后 `GET /openapi.json` 返回 HTTP 200。阶段 2 至此完成；该结论
不包含后续 24 实例、GPU 真实性、注册、真实推理、压力、恢复和完整泳道。

## 阶段 3：平台和逐卡算子拓扑

```bash
BASELINE_OPERATOR_IDS="$RELEASE_ROOT/container-maintenance/baseline-operator-container-ids.txt"
NEW_OPERATOR_IDS="$RELEASE_ROOT/container-maintenance/new-operator-container-ids.txt"
LEDGER_DIR="$(dirname "$BASELINE_OPERATOR_IDS")"
test "$LEDGER_DIR" = "$(dirname "$NEW_OPERATOR_IDS")"
test -d "$LEDGER_DIR"

OPERATOR_LEDGER_TEMPS=()
cleanup_operator_ledger_temps() {
  if ((${#OPERATOR_LEDGER_TEMPS[@]})); then
    rm -f -- "${OPERATOR_LEDGER_TEMPS[@]}"
  fi
}
trap cleanup_operator_ledger_temps EXIT

if ! OPERATOR_SERVICE_ALLOWLIST_TMP="$(
  mktemp "$LEDGER_DIR/.operator-service-allowlist.XXXXXX"
)"; then
  echo "无法创建权威算子 service allowlist 临时文件" >&2
  exit 1
fi
OPERATOR_LEDGER_TEMPS+=("$OPERATOR_SERVICE_ALLOWLIST_TMP")
if ! docker compose -f deploy/docker-compose.operators.yml --profile '*' \
  config --services | LC_ALL=C sort -u >"$OPERATOR_SERVICE_ALLOWLIST_TMP"; then
  echo "无法从权威 Compose 解析算子 service allowlist" >&2
  exit 1
fi
if [[ "$(wc -l <"$OPERATOR_SERVICE_ALLOWLIST_TMP" | tr -d ' ')" != 24 ]]; then
  echo "权威算子 Compose 必须精确包含 24 个 service" >&2
  exit 1
fi

validate_operator_id_file() {
  local id_file="$1"
  local container_id actual_id
  while IFS= read -r container_id || [[ -n "$container_id" ]]
  do
    if [[ ! "$container_id" =~ ^[0-9a-f]{64}$ ]]; then
      echo "容器 ID 不是 64 位小写十六进制: $container_id" >&2
      return 1
    fi
    if ! actual_id="$(docker inspect -f '{{.Id}}' "$container_id")"; then
      echo "无法核验容器 ID: $container_id" >&2
      return 1
    fi
    if [[ "$actual_id" != "$container_id" ]]; then
      echo "容器 ID 与 docker inspect 不一致: $container_id" >&2
      return 1
    fi
  done <"$id_file"
  return 0
}

validate_operator_identity() {
  local container_id="$1" actual_id compose_project service_name allowlist_status=0
  if [[ ! "$container_id" =~ ^[0-9a-f]{64}$ ]]; then
    echo "容器 ID 不是 64 位小写十六进制: $container_id" >&2
    return 1
  fi
  if ! actual_id="$(docker inspect -f '{{.Id}}' "$container_id")"; then
    echo "无法核验容器 ID: $container_id" >&2
    return 1
  fi
  if [[ "$actual_id" != "$container_id" ]]; then
    echo "容器 ID 与 docker inspect 不一致: $container_id" >&2
    return 1
  fi
  if ! compose_project="$(
    docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$container_id"
  )"; then
    echo "无法核验容器 Compose project: $container_id" >&2
    return 1
  fi
  if [[ "$compose_project" != "algorithm-operators" ]]; then
    echo "拒绝非权威算子 Compose project: $compose_project ($container_id)" >&2
    return 1
  fi
  if ! service_name="$(
    docker inspect -f '{{ index .Config.Labels "com.docker.compose.service" }}' "$container_id"
  )"; then
    echo "无法核验容器 Compose service: $container_id" >&2
    return 1
  fi
  grep -Fqx -- "$service_name" "$OPERATOR_SERVICE_ALLOWLIST_TMP" || allowlist_status=$?
  case "$allowlist_status" in
    0)
      return 0
      ;;
    1)
      echo "拒绝不在权威 Compose allowlist 中的算子服务: $service_name ($container_id)" >&2
      return 1
      ;;
    *)
      echo "无法读取权威算子 service allowlist: $service_name ($container_id)" >&2
      return 1
      ;;
  esac
}

snapshot_current_operator_ids() {
  local output_file="$1"
  if ! docker compose -f deploy/docker-compose.operators.yml --profile '*' \
    ps --all --no-trunc -q | LC_ALL=C sort -u >"$output_file"; then
    echo "无法获取完整算子容器快照" >&2
    return 1
  fi
  if ! validate_operator_id_file "$output_file"; then
    echo "当前算子容器快照校验失败" >&2
    return 1
  fi
  return 0
}

assert_not_in_baseline() {
  local container_id="$1" grep_status=0
  grep -Fqx -- "$container_id" "$BASELINE_OPERATOR_IDS" || grep_status=$?
  case "$grep_status" in
    0)
      echo "拒绝将 baseline 容器视为本轮新增: $container_id" >&2
      return 1
      ;;
    1)
      return 0
      ;;
    *)
      echo "无法读取 baseline 账本以排除容器: $container_id" >&2
      return 1
      ;;
  esac
}

refresh_new_operator_ledger() {
  local CURRENT_TMP NEW_TMP container_id
  if ! validate_operator_id_file "$BASELINE_OPERATOR_IDS"; then
    echo "baseline 账本校验失败" >&2
    return 1
  fi
  if ! CURRENT_TMP="$(mktemp "$LEDGER_DIR/.current-operator-container-ids.XXXXXX")"; then
    echo "无法创建 current 快照临时文件" >&2
    return 1
  fi
  OPERATOR_LEDGER_TEMPS+=("$CURRENT_TMP")
  if ! NEW_TMP="$(mktemp "$LEDGER_DIR/.new-operator-container-ids.XXXXXX")"; then
    echo "无法创建 new ledger 临时文件" >&2
    return 1
  fi
  OPERATOR_LEDGER_TEMPS+=("$NEW_TMP")
  if ! snapshot_current_operator_ids "$CURRENT_TMP"; then
    return 1
  fi
  if ! comm -23 "$CURRENT_TMP" "$BASELINE_OPERATOR_IDS" >"$NEW_TMP"; then
    echo "无法计算 current 与 baseline 的差集" >&2
    return 1
  fi
  if ! validate_operator_id_file "$NEW_TMP"; then
    echo "新增容器差集校验失败" >&2
    return 1
  fi
  while IFS= read -r container_id || [[ -n "$container_id" ]]
  do
    if ! validate_operator_identity "$container_id"; then
      echo "新增容器身份校验失败: $container_id" >&2
      return 1
    fi
    if ! assert_not_in_baseline "$container_id"; then
      return 1
    fi
  done <"$NEW_TMP"
  if ! mv -f -- "$NEW_TMP" "$NEW_OPERATOR_IDS"; then
    echo "无法原子发布新增容器账本" >&2
    return 1
  fi
  rm -f -- "$CURRENT_TMP" || true
  return 0
}

start_operator_profile() {
  local profile="$1" up_status=0
  docker compose -f deploy/docker-compose.operators.yml --profile "$profile" \
    up -d || up_status=$?

  if ! refresh_new_operator_ledger; then
    echo "profile $profile 启动后无法安全刷新新增容器账本；已发布 baseline 保留且未发布损坏的 new ledger。" >&2
    echo "禁止执行 cleanup；待 Docker 恢复后基于 baseline 重新刷新账本。" >&2
    return 1
  fi
  if ((up_status != 0)); then
    echo "profile $profile 的 docker compose up 返回 ${up_status}，可能已 partial-up；新增容器账本已安全刷新，现按原退出码中止。" >&2
    return "$up_status"
  fi
  return 0
}

BASELINE_TMP="$(mktemp "$LEDGER_DIR/.baseline-operator-container-ids.XXXXXX")"
OPERATOR_LEDGER_TEMPS+=("$BASELINE_TMP")
NEW_TMP="$(mktemp "$LEDGER_DIR/.new-operator-container-ids.XXXXXX")"
OPERATOR_LEDGER_TEMPS+=("$NEW_TMP")
snapshot_current_operator_ids "$BASELINE_TMP"
mv -f -- "$BASELINE_TMP" "$BASELINE_OPERATOR_IDS"
: >"$NEW_TMP"
validate_operator_id_file "$NEW_TMP"
mv -f -- "$NEW_TMP" "$NEW_OPERATOR_IDS"

EXPECTED_GIT_SHA="$EXPECTED_GIT_SHA" \
  docker compose -f deploy/docker-compose.platform.yml up -d --build
docker compose -f deploy/docker-compose.platform.yml ps
deploy/scripts/preflight runtime --git-sha "$EXPECTED_GIT_SHA"

start_operator_profile gpu0
deploy/scripts/preflight operators --profile gpu0 --git-sha "$EXPECTED_GIT_SHA" \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --reports-root "$REPORT_ROOT"
start_operator_profile gpu1
deploy/scripts/preflight operators --profile gpu1 --git-sha "$EXPECTED_GIT_SHA" \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --reports-root "$REPORT_ROOT"
start_operator_profile gpu2
deploy/scripts/preflight operators --profile gpu2 --git-sha "$EXPECTED_GIT_SHA" \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --reports-root "$REPORT_ROOT"
start_operator_profile cpu
deploy/scripts/preflight operators --profile cpu --git-sha "$EXPECTED_GIT_SHA" \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --reports-root "$REPORT_ROOT"
docker compose -f deploy/docker-compose.operators.yml ps

test "$(wc -l <"$NEW_OPERATOR_IDS" | tr -d ' ')" = 24
```

阶段 1 到阶段 6 必须在发布变量块启用 strict mode 的同一 Bash 会话中按顺序连续执行。
`set -euo pipefail` 保证预检、快照、暂停、模型发布、构建、Compose、管道、排序、
`comm` 或 ID 校验任何一步失败时立即停止。baseline 允许为空；所有非空 ID 必须是
64 位小写十六进制且与
`docker inspect .Id` 精确一致。new 差集发布前还必须由统一
`validate_operator_identity` 核验 Compose project 为 `algorithm-operators`，并精确匹配
权威 Compose `config --services` 动态生成的 24 项 allowlist。baseline、每次 current
快照和 new 差集均先写入
`container-maintenance/` 同目录的 `mktemp` 文件；全部校验后才原子 `mv`。任何失败
都不得截断已发布的权威 ledger。

`start_operator_profile` 不依赖 `set -e` 对 `docker compose up` 的默认处理。它先保留
Compose 退出码，无论成功或 partial-up 失败都先刷新原子 ledger；刷新成功后，
若 Compose 失败则按原退出码返回，由严格模式中止后续 profile 和 preflight。若 ledger
刷新自身失败，不发布临时结果，保留已发布 baseline/new ledger；此时禁止执行
cleanup，必须等 Docker 恢复后基于 baseline 重新执行 `refresh_new_operator_ledger`。

权威 Compose 必须保持 24 个实例：18 个 GPU 实例（六类 GPU 算子 × 三卡）和
6 个 CPU 实例（PPT Slice × 三、Text Analysis × 三）。每个容器一个 Uvicorn
worker；同一实例 ID 不得被两个容器复用。容器配置只读挂载 `deploy/config/operators`，
模型和业务结果分别遵守 `/data/course`、`/data/result` 边界。

部署前将仓库内 `deploy/endpoints.json` 和 `deploy/endpoints-full.json` 两份权威文件
复制到 Git 外 fixture 根。逐实例 GPU 触发命令使用外部 `endpoints.json`，八类 full
Smoke 使用外部 `endpoints-full.json`，两者不得互换。

`preflight runtime` 对四个平台最终镜像执行 attestation。每个 profile preflight 对所选
算子最终镜像执行 attestation，并验证运行容器身份、注册、首次心跳、`ONLINE` 和
`model_ready=true`。Smoke 的 `--git-sha` 只记录报告归属，不是镜像 attestation。

## 阶段 4：GPU 真实性证据

每个 GPU 容器必须在真实推理触发器存活期间采样。触发器是 JSON argv 数组，不经过
shell；输出只能写入当前 release 的 `gpu-instances/`，不能覆盖已有证据：

```bash
cat >/tmp/asr-offline-gpu0-trigger.json <<JSON
["/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/scripts/run-operator-smoke", "--release-tag", "${RELEASE_TAG}", "--git-sha", "${EXPECTED_GIT_SHA}", "--reports-root", "${REPORT_ROOT}", "--fixture-manifest", "/root/workspace/.algorithm-scheduling-fixtures/v1.0_260812/manifest.json", "--external-fixture-root", "/root/workspace/.algorithm-scheduling-fixtures/v1.0_260812", "--fixture-target-root", "/data/course/_harness/fixtures", "--result-root", "/data/result/_harness", "--endpoints-json", "/root/workspace/.algorithm-scheduling-fixtures/v1.0_260812/endpoints.json", "--operator", "asr_offline", "--instance", "asr-offline-gpu0", "--run-id", "auto", "--repeat", "1", "--hold-seconds", "30"]
JSON

deploy/scripts/verify-gpu-instance \
  --container asr-offline-gpu0 --instance-id asr-offline-gpu0 \
  --physical-gpu 0 --process-name asr_offline \
  --trigger-file /tmp/asr-offline-gpu0-trigger.json \
  --output "$RELEASE_ROOT/gpu-instances/asr-offline-gpu0.json"
```

对 18 个 GPU 实例逐一替换容器、物理 GPU 和算子名。通过条件同时包括：容器只见
一张目标卡、UUID 与宿主一致、`cuda:0`/框架设备证据、`nvidia-smi` 中目标算子
进程名、宿主 CUDA PID、`docker top` 映射、完整 64 位 cgroup ID 和 NSpid。停止容器
后必须立即用 `--assert-stopped --evidence <prior-json>` 将残留检查写入 `recovery/`，
随后立即重启同一实例，等待它重新完成注册、首次心跳、`ONLINE`、`model_ready=true`，
再验证下一个实例。示例的停止后半程为：

```bash
docker stop asr-offline-gpu0
deploy/scripts/verify-gpu-instance \
  --container asr-offline-gpu0 --instance-id asr-offline-gpu0 \
  --physical-gpu 0 --process-name asr_offline --assert-stopped \
  --evidence "$RELEASE_ROOT/gpu-instances/asr-offline-gpu0.json" \
  --output "$RELEASE_ROOT/recovery/asr-offline-gpu0-stopped.json"
docker restart asr-offline-gpu0
deploy/scripts/verify-operator-registration \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --git-sha "$EXPECTED_GIT_SHA" \
  --reports-root "$REPORT_ROOT" --instance asr-offline-gpu0
```

不得把已经取得停止证据的实例留在停止状态。每次恢复只使用
`verify-operator-registration --instance <当前实例>` 等待该实例重新注册、
首次心跳、`ONLINE` 和 `model_ready=true`，生成独立的 instance 报告。不得重复运行
已完成的 profile preflight，否则会与 write-once profile 报告冲突。18 个实例逐一
恢复后，最终验收必须保持 18 个 GPU 实例和 6 个 CPU 实例同时运行并 ONLINE。

## 阶段 5：注册、Smoke 和报告

24 实例必须经历“注册 -> 首次心跳 -> ONLINE -> model_ready=true -> capability/
GPU 标签匹配”；只有注册响应不能通过：

```bash
deploy/scripts/preflight operators --full --git-sha "$EXPECTED_GIT_SHA" \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --reports-root "$REPORT_ROOT"
```

`preflight operators --full` 内部已执行全 24 实例注册校验并生成 full 报告；不再单独
执行无 `--instance`/`--profile` 的 `verify-operator-registration`，避免写入同一份
write-once full 报告。

八类 Smoke 使用 `deploy/operator-smoke-cases.json`、`operator-smoke-fixtures.json`
和外部 fixture manifest。ASR Offline 调用 v1.1.8；ASR Online 使用 WebSocket；
OCR/VBas/FaceRec/ScreenDet 使用单图；PPT 必须使用冻结的本地 `video_path`，等待一次
终态 manifest 回调；Text Analysis 调用 `extract_keywords` 与 `course_overviews`。
缺失的 ASR/VBas/PPT fixture 必须写成“未执行及原因”，不能改用随意媒体冒充基准。

PPT 终态回调的 `19090` 是 Smoke 期间的 Harness-only 临时端口，不是平台北向端口。
从权威 `algorithm-platform` Docker bridge 动态读取 gateway，同时作为监听地址和
算子容器可访问的广播地址；不得绑定 `0.0.0.0` 或服务器物理网卡。
`run-operator-smoke` 只在 PPT 用例中启动该监听，该次 Smoke 结束后必须关闭监听。

CPU profile 不能只抽测 cpu0。先对六个 CPU 实例分别 Smoke：

```bash
ALGORITHM_PLATFORM_GATEWAY="$(
  docker network inspect algorithm-platform \
    --format '{{(index .IPAM.Config 0).Gateway}}'
)"
test -n "$ALGORITHM_PLATFORM_GATEWAY"
test "$ALGORITHM_PLATFORM_GATEWAY" != "<no value>"

for operator_instance in \
  ppt_slice:ppt-slice-cpu0 ppt_slice:ppt-slice-cpu1 ppt_slice:ppt-slice-cpu2 \
  text_analysis:text-analysis-cpu0 text_analysis:text-analysis-cpu1 text_analysis:text-analysis-cpu2
do
  operator_code="${operator_instance%%:*}"
  instance_id="${operator_instance#*:}"
  deploy/scripts/run-operator-smoke \
    --release-tag "$RELEASE_TAG" --git-sha "$EXPECTED_GIT_SHA" \
    --reports-root "$REPORT_ROOT" \
    --fixture-manifest /root/workspace/.algorithm-scheduling-fixtures/v1.0_260812/manifest.json \
    --external-fixture-root /root/workspace/.algorithm-scheduling-fixtures/v1.0_260812 \
    --fixture-target-root /data/course/_harness/fixtures \
    --result-root /data/result/_harness \
    --callback-listen-host "$ALGORITHM_PLATFORM_GATEWAY" \
    --callback-advertise-base-url "http://${ALGORITHM_PLATFORM_GATEWAY}:19090" \
    --endpoints-json /root/workspace/.algorithm-scheduling-fixtures/v1.0_260812/endpoints.json \
    --operator "$operator_code" --instance "$instance_id" --run-id auto
done
```

六个 CPU 结果齐备后，确认 FaceRec 三个容器同时 running，并用一份独立的三实例
注册报告确认它们同时 `ONLINE` 且 `model_ready=true`：

```bash
for face_instance in facerec-gpu0 facerec-gpu1 facerec-gpu2
do
  test "$(docker inspect -f '{{.State.Running}}' "$face_instance")" = true
done
deploy/scripts/verify-operator-registration \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --git-sha "$EXPECTED_GIT_SHA" \
  --reports-root "$REPORT_ROOT" \
  --instance facerec-gpu0 --instance facerec-gpu1 --instance facerec-gpu2
```

最后且只执行一次八类 full Smoke；`endpoints-full.json` 中的 FaceRec 三 URL 会在同一
用例内共同参与人物创建、识别和清理：

```bash
deploy/scripts/run-operator-smoke \
  --release-tag "$RELEASE_TAG" --git-sha "$EXPECTED_GIT_SHA" \
  --reports-root "$REPORT_ROOT" \
  --fixture-manifest /root/workspace/.algorithm-scheduling-fixtures/v1.0_260812/manifest.json \
  --external-fixture-root /root/workspace/.algorithm-scheduling-fixtures/v1.0_260812 \
  --fixture-target-root /data/course/_harness/fixtures \
  --result-root /data/result/_harness \
  --callback-listen-host "$ALGORITHM_PLATFORM_GATEWAY" \
  --callback-advertise-base-url "http://${ALGORITHM_PLATFORM_GATEWAY}:19090" \
  --endpoints-json /root/workspace/.algorithm-scheduling-fixtures/v1.0_260812/endpoints-full.json
```

六个 CPU 逐实例结果、FaceRec 三实例同时就绪结果、八类 full
结果、四个 profile preflight 和最终 full preflight 是互补证据，不能相互替代。

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

canonical 2B 场景绝不对 platform/infrastructure 执行 `down` 或 `stop`。只停止
`$NEW_OPERATOR_IDS` 中本轮新增的算子容器，不删除容器。清理前重新校验 baseline
和 new ledger；每个 ID 还必须再次确认为 64 位小写十六进制、不在 baseline、
`docker inspect .Id` 精确一致，且 Compose project/service 标签匹配。禁止对 ledger 外容器执行操作：

```bash
validate_operator_id_file "$BASELINE_OPERATOR_IDS"
validate_operator_id_file "$NEW_OPERATOR_IDS"
while IFS= read -r container_id || [[ -n "$container_id" ]]
do
  if ! validate_operator_identity "$container_id"; then
    exit 1
  fi
  if ! assert_not_in_baseline "$container_id"; then
    exit 1
  fi
done <"$NEW_OPERATOR_IDS"
while IFS= read -r container_id || [[ -n "$container_id" ]]
do
  docker stop "$container_id"
done <"$NEW_OPERATOR_IDS"
deploy/scripts/restore-existing-containers "$SNAPSHOT" "${SNAPSHOT}.paused.jsonl"
```

清理采用两遍处理：第一遍复用发布前的 `validate_operator_identity` 并排除 baseline；
只有整份 new ledger 全部通过后，第二遍才逐个执行 `docker stop`。任一身份不合规时，
不得停止其中任何容器。

清理后确认原 `ocr-v6-amd` 恢复到快照状态。平台与四类基础设施继续运行；不得 prune、
不得删除卷、不得删除 `/data/result`。whole-stack `down` 只允许出现在与本服务器隔离的
本地开发环境，不属于本场景。

最后渲染当前 release 的 JSON/Markdown 汇总；renderer 要求通过用例有证据文件、
未执行用例有中文原因、所有用例使用同一 release/SHA，并拒绝跨目录或包含敏感 token：

```bash
.venv/bin/python scripts/render_milestone_2b_report.py \
  --input "$RELEASE_ROOT/summary/cases.json" \
  --release-root "$RELEASE_ROOT" \
  --output-json "$RELEASE_ROOT/summary/report.json" \
  --output-markdown "$RELEASE_ROOT/summary/report.md"
```

## 当前未执行声明

本地静态、单元和部署合同测试已执行。目标 x86_64 服务器已取得旧 SHA
`e65dd576b3b53b73a874bb131449ef031423057b` 的模型资产校验和八算子镜像构建证据；
该历史证据不能代替本轮最终发布 SHA 的验收。最终 SHA 对应的四平台/八算子镜像
重建、基础设施与平台运行状态、runtime attestation、24 个算子实例同时 ONLINE、
18 个 GPU 实例真实性、八类真实模型/课程媒体 Smoke、反例、压力、恢复和完整
离线/在线泳道均待本轮现场证据确认。后续报告必须继续逐项给出真实证据或中文
未执行原因。

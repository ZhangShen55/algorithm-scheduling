# 里程碑 2B 三卡部署验证场景

## 目标与证据边界

本场景是里程碑 2B 的部署验证入口，目标是验证 x86_64、三张 NVIDIA GPU
服务器上的八类算子、24 个容器实例、四个平台服务和四类基础设施。执行顺序
固定为：

```text
report init + Harness .venv -> preflight -> snapshot/pause -> infrastructure -> model staging/verify
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
PREVIOUS_RELEASE_ROOT="${PREVIOUS_RELEASE_ROOT:-}"
PLATFORM_WAIT_TIMEOUT_SECONDS="${PLATFORM_WAIT_TIMEOUT_SECONDS:-180}"
OPERATOR_LIFECYCLE_LOCK_PID=
OPERATOR_LIFECYCLE_LOCK_CONTROL_FD=
OPERATOR_LIFECYCLE_LOCK_READY_FD=

if ! [[ "$PLATFORM_WAIT_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] ||
  ((${#PLATFORM_WAIT_TIMEOUT_SECONDS} > 4)) ||
  ((PLATFORM_WAIT_TIMEOUT_SECONDS > 3600)); then
  echo "PLATFORM_WAIT_TIMEOUT_SECONDS 必须是 1 到 3600 之间的整数" >&2
  exit 1
fi

validate_previous_release_root() {
  local previous_sha
  if [[ -z "$PREVIOUS_RELEASE_ROOT" ]]; then
    return 0
  fi
  previous_sha="${PREVIOUS_RELEASE_ROOT##*/}"
  if [[ ! "$previous_sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "PREVIOUS_RELEASE_ROOT 必须以 40 位小写 Git SHA 结尾" >&2
    return 1
  fi
  if [[ "$previous_sha" == "$EXPECTED_GIT_SHA" ]]; then
    echo "PREVIOUS_RELEASE_ROOT 必须属于不同 Git SHA" >&2
    return 1
  fi
  if [[ "$PREVIOUS_RELEASE_ROOT" != \
    "$REPORT_ROOT/milestone-2b/releases/$RELEASE_TAG/$previous_sha" ]]; then
    echo "PREVIOUS_RELEASE_ROOT 必须属于同一 REPORT_ROOT/release tag" >&2
    return 1
  fi
  if [[ ! -d "$PREVIOUS_RELEASE_ROOT" || -L "$PREVIOUS_RELEASE_ROOT" ]]; then
    echo "PREVIOUS_RELEASE_ROOT 必须是非 symlink 目录" >&2
    return 1
  fi
}

acquire_operator_lifecycle_lock() {
  local release_tag_root lock_path lock_status holder_status=0
  if operator_lifecycle_lock_is_held; then
    return 0
  fi
  if [[ ! "$EXPECTED_GIT_SHA" =~ ^[0-9a-f]{40}$ ]]; then
    echo "EXPECTED_GIT_SHA 必须是 40 位小写 Git SHA" >&2
    return 1
  fi
  release_tag_root="$REPORT_ROOT/milestone-2b/releases/$RELEASE_TAG"
  if [[ "$RELEASE_ROOT" != "$release_tag_root/$EXPECTED_GIT_SHA" ]]; then
    echo "RELEASE_ROOT 与 REPORT_ROOT/release tag/Git SHA 不一致" >&2
    return 1
  fi
  if [[ ! -d "$release_tag_root" || -L "$release_tag_root" ]]; then
    echo "release tag 目录必须是非 symlink 目录" >&2
    return 1
  fi
  lock_path="$release_tag_root/.operator-lifecycle.lock"
  coproc OPERATOR_LIFECYCLE_LOCK_HOLDER {
    "$DEPLOY_PYTHON" deploy/scripts/operator_lifecycle.py hold-lock \
      --release-tag-root "$release_tag_root" --lock-path "$lock_path"
  }
  OPERATOR_LIFECYCLE_LOCK_PID="$OPERATOR_LIFECYCLE_LOCK_HOLDER_PID"
  OPERATOR_LIFECYCLE_LOCK_READY_FD="${OPERATOR_LIFECYCLE_LOCK_HOLDER[0]}"
  OPERATOR_LIFECYCLE_LOCK_CONTROL_FD="${OPERATOR_LIFECYCLE_LOCK_HOLDER[1]}"
  if ! IFS= read -r lock_status <&"$OPERATOR_LIFECYCLE_LOCK_READY_FD"; then
    wait "$OPERATOR_LIFECYCLE_LOCK_PID" || holder_status=$?
    release_operator_lifecycle_lock
    echo "无法获取 release-tag 级算子维护锁 (status=$holder_status)" >&2
    return 1
  fi
  exec {OPERATOR_LIFECYCLE_LOCK_READY_FD}<&-
  if [[ "$lock_status" != "LOCKED" ]] || ! operator_lifecycle_lock_is_held; then
    release_operator_lifecycle_lock
    echo "release-tag 级算子维护锁未完成安全握手" >&2
    return 1
  fi
}

operator_lifecycle_lock_is_held() {
  [[ -n "${OPERATOR_LIFECYCLE_LOCK_PID:-}" && \
    -n "${OPERATOR_LIFECYCLE_LOCK_CONTROL_FD:-}" ]] && \
    kill -0 "$OPERATOR_LIFECYCLE_LOCK_PID" 2>/dev/null
}

release_operator_lifecycle_lock() {
  if [[ -n "${OPERATOR_LIFECYCLE_LOCK_READY_FD:-}" ]]; then
    exec {OPERATOR_LIFECYCLE_LOCK_READY_FD}<&- || true
  fi
  if [[ -n "${OPERATOR_LIFECYCLE_LOCK_CONTROL_FD:-}" ]]; then
    exec {OPERATOR_LIFECYCLE_LOCK_CONTROL_FD}>&- || true
  fi
  if [[ -n "${OPERATOR_LIFECYCLE_LOCK_PID:-}" ]]; then
    wait "$OPERATOR_LIFECYCLE_LOCK_PID" || true
  fi
  OPERATOR_LIFECYCLE_LOCK_PID=
  OPERATOR_LIFECYCLE_LOCK_CONTROL_FD=
  OPERATOR_LIFECYCLE_LOCK_READY_FD=
  return 0
}

trap release_operator_lifecycle_lock EXIT
```

首次发布保持 `PREVIOUS_RELEASE_ROOT` 为空。同一 release tag 换 SHA 续跑时，
在执行上述代码块前显式导出上一 SHA 的绝对 release 目录；例如
`PREVIOUS_RELEASE_ROOT="$PWD/deploy/reports/milestone-2b/releases/v1.0_260812/<previous-40-char-sha>"`。
不允许自动挑选“最新”目录。

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

clean clone 不携带项目 Python 环境。完成 release 目录初始化后、执行任何
`preflight`、`verify-operator-registration` 或 `run-operator-smoke` 前，必须使用服务器
`python3` 创建项目根 `.venv`，并只安装 `pyproject.toml` 的基础依赖。不得把“不使用
`.env` 配置文件”误解为“不需要 `.venv` Python 环境”。

版本证据先写入当前 release `preflight/` 下的同目录临时文件；只有 Python 和三个
Harness 依赖均可导入并成功取得版本后，才原子发布正式 JSON。任何一步失败都由 strict
mode 中止后续 preflight、profile 和 Smoke：

```bash
python3 -m venv "$PWD/.venv"
"$PWD/.venv/bin/python" -m pip install .

HARNESS_RUNTIME_EVIDENCE="$RELEASE_ROOT/preflight/harness-python-runtime.json"
HARNESS_RUNTIME_TMP="$(
  mktemp "$RELEASE_ROOT/preflight/.harness-python-runtime.XXXXXX"
)"
if ! (
  "$PWD/.venv/bin/python" - <<'PY' >"$HARNESS_RUNTIME_TMP"
from importlib import metadata
import json
import sys

import httpx
import websockets
import yaml

evidence = {
    "python_executable": sys.executable,
    "python_version": sys.version.split()[0],
    "dependencies": {
        "httpx": metadata.version("httpx"),
        "PyYAML": metadata.version("PyYAML"),
        "websockets": metadata.version("websockets"),
    },
}
print(json.dumps(evidence, sort_keys=True))
PY
); then
  rm -f -- "$HARNESS_RUNTIME_TMP"
  exit 1
fi
chmod 0600 "$HARNESS_RUNTIME_TMP"
mv -f -- "$HARNESS_RUNTIME_TMP" "$HARNESS_RUNTIME_EVIDENCE"
export DEPLOY_PYTHON="$PWD/.venv/bin/python"
```

`$PWD` 在本场景中是 `algorithm-scheduling-platform` 的绝对路径，因此导出的
`DEPLOY_PYTHON` 是项目 `.venv` 的绝对解释器路径。后续 wrapper 不得回退到缺少
`httpx`、PyYAML 或 `websockets` 的系统 Python；证据 JSON 与本次 release/SHA 一一对应。

`model-assets.manifest.json` 只归档到 Git 外的受限目录；报告只记录模型根、文件
数和总字节数，不记录逐文件哈希或密钥元数据。ASR Offline、ASR Online、OCR、VBas、
FaceRec、ScreenDet 六个模型根必须由外部 manifest 冻结；PPT Slice 和 Text Analysis
没有本地模型根。

## 阶段 1：服务器预检、快照和暂停

锁必须在 host preflight 和任何 snapshot/pause 之前以非阻塞方式获取，并由
同一 Bash 会话持有到阶段 6 的唯一 restore 完成。Python holder 以目录
FD 和 `O_NOFOLLOW` 打开锁，校验普通文件、当前 UID、`0600`、单链接及 inode
后才把 FD 交给 `flock -n`；父 shell 通过控制管道持有 holder。

fresh release 创建新维护账本；同 SHA 已有本地 snapshot/paused 时原地复用。
换 SHA 时从立即前驱读取直接账本或 provenance：A→B→C 中 C 的 provenance
记录立即前驱 B，但 authority path 仍指向原 snapshot 所在的 A。已存在的
provenance 必须为当前 UID 所有的非 symlink `0400` 普通文件，与本次
`PREVIOUS_RELEASE_ROOT` 不一致时 fail closed，不得改绑。

```bash
acquire_operator_lifecycle_lock
validate_previous_release_root

if ! MAINTENANCE_STATE_OUTPUT="$(
  "$DEPLOY_PYTHON" deploy/scripts/operator_lifecycle.py resolve-maintenance \
    --report-root "$REPORT_ROOT" --release-tag "$RELEASE_TAG" \
    --release-root "$RELEASE_ROOT" \
    --previous-release-root "$PREVIOUS_RELEASE_ROOT"
)"; then
  exit 1
fi
mapfile -t MAINTENANCE_STATE_FIELDS <<<"$MAINTENANCE_STATE_OUTPUT"
if ((${#MAINTENANCE_STATE_FIELDS[@]} != 4)); then
  echo "算子维护状态解析结果不完整" >&2
  exit 1
fi
MAINTENANCE_ACTION="${MAINTENANCE_STATE_FIELDS[0]}"
MAINTENANCE_SOURCE_ROOT="${MAINTENANCE_STATE_FIELDS[1]}"
SNAPSHOT="${MAINTENANCE_STATE_FIELDS[2]}"
PAUSED_LEDGER="${MAINTENANCE_STATE_FIELDS[3]}"

AUTHORIZED_OCCUPIED_ENDPOINTS=
if [[ "$MAINTENANCE_ACTION" != "fresh" ]]; then
  if ! AUTHORIZED_OCCUPIED_ENDPOINTS="$(
    "$DEPLOY_PYTHON" deploy/scripts/operator_lifecycle.py \
      authoritative-published-endpoints \
      --platform-compose-file deploy/docker-compose.platform.yml \
      --operator-compose-file deploy/docker-compose.operators.yml
  )"; then
    exit 1
  fi
fi
AUTHORIZED_OCCUPIED_ENDPOINTS="$AUTHORIZED_OCCUPIED_ENDPOINTS" \
  EXPECTED_GIT_SHA="$EXPECTED_GIT_SHA" deploy/scripts/preflight host \
  >"$RELEASE_ROOT/preflight/preflight.log" 2>&1

if [[ "$MAINTENANCE_ACTION" == "inherit" ]]; then
  "$DEPLOY_PYTHON" deploy/scripts/operator_lifecycle.py publish-provenance \
    --report-root "$REPORT_ROOT" --release-tag "$RELEASE_TAG" \
    --release-root "$RELEASE_ROOT" \
    --source-release-root "$MAINTENANCE_SOURCE_ROOT" \
    --snapshot "$SNAPSHOT" --paused "$PAUSED_LEDGER"
elif [[ "$MAINTENANCE_ACTION" == "fresh" ]]; then
  deploy/scripts/snapshot-existing-containers "$SNAPSHOT"
  docker inspect ocr-v6-amd \
    >"$RELEASE_ROOT/container-maintenance/ocr-v6-amd-before.json"
  deploy/scripts/pause-existing-containers "$SNAPSHOT" ocr-v6-amd
fi
```

确认精确容器身份后，fresh 路径只暂停用户已允许的原 `ocr-v6-amd`；不要使用
空选择器或按宽泛名称匹配。权威暂停账本固定为 `$PAUSED_LEDGER`，必须保留到
恢复完成。previous 路径只以不可替换方式写入权限 `0400` 的指针证据，不复制
可变 paused ledger。fresh 路径强制以空 `AUTHORIZED_OCCUPIED_ENDPOINTS` 运行 host
preflight；只有续跑才从权威 platform/operator Compose 渲染结果和按 service
限定的运行容器中，经完整 ID、running、project/service 标签及端口映射校验后
精确派生已占用的“监听地址+端口”端点。同端口的任何额外地址或地址族监听仍由
preflight 逐条拒绝。预检失败时停止
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
if ! operator_lifecycle_lock_is_held; then
  echo "阶段 3 拒绝在未持有 release-tag 级维护锁时执行" >&2
  exit 1
fi
BASELINE_OPERATOR_IDS="$RELEASE_ROOT/container-maintenance/baseline-operator-container-ids.txt"
NEW_OPERATOR_IDS="$RELEASE_ROOT/container-maintenance/new-operator-container-ids.txt"
LEDGER_DIR="$(dirname "$BASELINE_OPERATOR_IDS")"
test "$LEDGER_DIR" = "$(dirname "$NEW_OPERATOR_IDS")"
test -d "$LEDGER_DIR"

BASELINE_LEDGER_PRESENT=0
NEW_LEDGER_PRESENT=0
[[ -e "$BASELINE_OPERATOR_IDS" || -L "$BASELINE_OPERATOR_IDS" ]] && \
  BASELINE_LEDGER_PRESENT=1
[[ -e "$NEW_OPERATOR_IDS" || -L "$NEW_OPERATOR_IDS" ]] && \
  NEW_LEDGER_PRESENT=1
if ((BASELINE_LEDGER_PRESENT != NEW_LEDGER_PRESENT)); then
  echo "当前 release 存在 partial ledger；baseline/new 必须同时存在或同时不存在" >&2
  exit 1
fi

OPERATOR_LEDGER_TEMPS=()
cleanup_operator_ledger_temps() {
  if ((${#OPERATOR_LEDGER_TEMPS[@]})); then
    rm -f -- "${OPERATOR_LEDGER_TEMPS[@]}"
  fi
}
cleanup_operator_lifecycle() {
  local original_status=$?
  cleanup_operator_ledger_temps || true
  release_operator_lifecycle_lock || true
  return "$original_status"
}
trap cleanup_operator_lifecycle EXIT

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

validate_operator_ledger_file() {
  local id_file="$1" container_id
  if [[ ! -f "$id_file" || -L "$id_file" ]]; then
    echo "算子账本必须是非 symlink 普通文件: $id_file" >&2
    return 1
  fi
  if ! LC_ALL=C sort -u "$id_file" | cmp -s - "$id_file"; then
    echo "算子账本必须按字节序排序且 ID 唯一: $id_file" >&2
    return 1
  fi
  if ! validate_operator_id_file "$id_file"; then
    return 1
  fi
  while IFS= read -r container_id || [[ -n "$container_id" ]]
  do
    if validate_operator_identity "$container_id"; then
      :
    else
      echo "算子账本容器身份校验失败: $container_id" >&2
      return 1
    fi
  done <"$id_file"
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
  if ! validate_operator_ledger_file "$BASELINE_OPERATOR_IDS"; then
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

if ((BASELINE_LEDGER_PRESENT == 1)); then
  validate_operator_ledger_file "$BASELINE_OPERATOR_IDS"
  validate_operator_ledger_file "$NEW_OPERATOR_IDS"
  refresh_new_operator_ledger
elif [[ -n "$PREVIOUS_RELEASE_ROOT" ]]; then
  validate_previous_release_root
  if ! OPERATOR_LEDGER_STATE_OUTPUT="$(
    "$DEPLOY_PYTHON" deploy/scripts/operator_lifecycle.py resolve-operator-ledgers \
      --report-root "$REPORT_ROOT" --release-tag "$RELEASE_TAG" \
      --previous-release-root "$PREVIOUS_RELEASE_ROOT"
  )"; then
    exit 1
  fi
  mapfile -t OPERATOR_LEDGER_STATE_FIELDS <<<"$OPERATOR_LEDGER_STATE_OUTPUT"
  if ((${#OPERATOR_LEDGER_STATE_FIELDS[@]} != 3)); then
    echo "算子账本解析结果不完整" >&2
    exit 1
  fi
  OPERATOR_LEDGER_SOURCE_ROOT="${OPERATOR_LEDGER_STATE_FIELDS[0]}"
  PREVIOUS_BASELINE_OPERATOR_IDS="${OPERATOR_LEDGER_STATE_FIELDS[1]}"
  PREVIOUS_NEW_OPERATOR_IDS="${OPERATOR_LEDGER_STATE_FIELDS[2]}"
  validate_operator_ledger_file "$PREVIOUS_BASELINE_OPERATOR_IDS"
  validate_operator_ledger_file "$PREVIOUS_NEW_OPERATOR_IDS"

  INHERIT_CURRENT_TMP="$(
    mktemp "$LEDGER_DIR/.inherit-current-operator-container-ids.XXXXXX"
  )"
  OPERATOR_LEDGER_TEMPS+=("$INHERIT_CURRENT_TMP")
  INHERIT_NEW_TMP="$(
    mktemp "$LEDGER_DIR/.inherit-new-operator-container-ids.XXXXXX"
  )"
  OPERATOR_LEDGER_TEMPS+=("$INHERIT_NEW_TMP")
  snapshot_current_operator_ids "$INHERIT_CURRENT_TMP"
  if ! comm -23 "$INHERIT_CURRENT_TMP" "$PREVIOUS_BASELINE_OPERATOR_IDS" \
    >"$INHERIT_NEW_TMP"; then
    echo "无法重算 current 与 previous baseline 的差集" >&2
    exit 1
  fi
  if ! cmp -s "$INHERIT_NEW_TMP" "$PREVIOUS_NEW_OPERATOR_IDS"; then
    echo "current - previous baseline 必须与 previous new ledger 精确一致" >&2
    exit 1
  fi

  BASELINE_TMP="$(mktemp "$LEDGER_DIR/.baseline-operator-container-ids.XXXXXX")"
  OPERATOR_LEDGER_TEMPS+=("$BASELINE_TMP")
  if ! cp -- "$PREVIOUS_BASELINE_OPERATOR_IDS" "$BASELINE_TMP"; then
    echo "无法继承 previous baseline 账本" >&2
    exit 1
  fi
  chmod 0600 "$BASELINE_TMP"
  if ! cmp -s "$BASELINE_TMP" "$PREVIOUS_BASELINE_OPERATOR_IDS"; then
    echo "previous baseline 继承内容不一致" >&2
    exit 1
  fi
  mv -f -- "$BASELINE_TMP" "$BASELINE_OPERATOR_IDS"
  refresh_new_operator_ledger
else
  BASELINE_TMP="$(mktemp "$LEDGER_DIR/.baseline-operator-container-ids.XXXXXX")"
  OPERATOR_LEDGER_TEMPS+=("$BASELINE_TMP")
  NEW_TMP="$(mktemp "$LEDGER_DIR/.new-operator-container-ids.XXXXXX")"
  OPERATOR_LEDGER_TEMPS+=("$NEW_TMP")
  snapshot_current_operator_ids "$BASELINE_TMP"
  validate_operator_ledger_file "$BASELINE_TMP"
  mv -f -- "$BASELINE_TMP" "$BASELINE_OPERATOR_IDS"
  : >"$NEW_TMP"
  validate_operator_ledger_file "$NEW_TMP"
  mv -f -- "$NEW_TMP" "$NEW_OPERATOR_IDS"
fi

EXPECTED_GIT_SHA="$EXPECTED_GIT_SHA" \
  docker compose -f deploy/docker-compose.platform.yml up -d --build --wait --wait-timeout "${PLATFORM_WAIT_TIMEOUT_SECONDS:-180}"
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

当前 release 的 baseline/new 要么同时不存在，要么同时为非 symlink 普通文件；
只存在一个时 fail closed。两者已完整存在表示同 SHA 恢复：保留原 baseline，
只按当前 Docker 状态刷新 new。新 SHA 且显式给出 `PREVIOUS_RELEASE_ROOT` 时，
previous root 必须属于同一 `REPORT_ROOT`/release tag 且以不同的 40 位 SHA 结尾。
只读 `resolve-operator-ledgers` 从该立即前驱开始，遇到最近的完整 baseline/new 对即返回；
没有账本时只允许沿严格验证的 maintenance provenance `source_release_root` 回溯。
任一候选只有一份账本、provenance 形成环或最终没有完整账本祖先时均 fail closed。
resolver 不得修改当前或祖先 provenance；因此 A（snapshot/paused）→B（完整算子账本）
→C（仅 provenance）→D（当前）中，D 的 maintenance provenance 仍记录 C 且 authority
仍为 A，阶段 3 仅把 B 作为账本来源。

resolver 返回的 baseline/new 必须按字节序排序、ID 唯一，并通过 inspect 与 Compose
project/service 身份校验。只有重算的 `current - resolved baseline` 与 resolved new
逐字节一致时，才原子继承 resolved baseline 并立即刷新当前 new。因此旧 SHA
启动的算子仍属于本轮可清理集合；Compose 以同 service 替换容器 ID 后，下一次
刷新会用新 ID 替换账本记录，不通过删除容器规避校验。

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
平台 `up` 使用 Compose 的有界健康等待；默认最多等待 180 秒，可通过
`PLATFORM_WAIT_TIMEOUT_SECONDS` 在 1 到 3600 秒内调整。只有四个平台服务均达到各自健康条件后才执行
runtime preflight，避免容器刚进入 running、应用仍在启动时产生瞬态连接失败。

## 阶段 4：GPU 真实性证据

每个 GPU 容器必须在真实推理触发器存活期间采样。触发器是 JSON argv 数组，不经过
shell；输出只能写入当前 release 的 `gpu-instances/`，不能覆盖已有证据：

```bash
resolve_operator_container_id() {
  local service_name="$1"
  local -a container_ids=()
  local container_id actual_id compose_project compose_service
  mapfile -t container_ids < <(
    docker compose -f deploy/docker-compose.operators.yml \
      ps --all --no-trunc -q "$service_name"
  )
  if [[ "${#container_ids[@]}" -ne 1 ]]; then
    echo "权威 Compose 中 ${service_name} 必须精确对应一个容器" >&2
    return 1
  fi
  container_id="${container_ids[0]}"
  actual_id="$(docker inspect -f '{{.Id}}' "$container_id")" || return 1
  compose_project="$(
    docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$container_id"
  )" || return 1
  compose_service="$(
    docker inspect -f '{{ index .Config.Labels "com.docker.compose.service" }}' "$container_id"
  )" || return 1
  if [[ ! "$container_id" =~ ^[0-9a-f]{64}$ || "$actual_id" != "$container_id" || \
        "$compose_project" != "algorithm-operators" || "$compose_service" != "$service_name" ]]; then
    echo "算子 Compose 容器身份不匹配: ${service_name}" >&2
    return 1
  fi
  printf '%s\n' "$container_id"
}

service_name=asr-offline-gpu0
instance_id=asr-offline-gpu0
container_id="$(resolve_operator_container_id "$service_name")"

cat >/tmp/asr-offline-gpu0-trigger.json <<JSON
["/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/scripts/run-operator-smoke", "--release-tag", "${RELEASE_TAG}", "--git-sha", "${EXPECTED_GIT_SHA}", "--reports-root", "${REPORT_ROOT}", "--fixture-manifest", "/root/workspace/.algorithm-scheduling-fixtures/v1.0_260812/manifest.json", "--external-fixture-root", "/root/workspace/.algorithm-scheduling-fixtures/v1.0_260812", "--fixture-target-root", "/data/course/_harness/fixtures", "--result-root", "/data/result", "--endpoints-json", "/root/workspace/.algorithm-scheduling-fixtures/v1.0_260812/endpoints.json", "--operator", "asr_offline", "--instance", "asr-offline-gpu0", "--run-id", "auto", "--repeat", "1", "--hold-seconds", "30"]
JSON

deploy/scripts/verify-gpu-instance \
  --container "$container_id" --instance-id "$instance_id" \
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
docker stop "$container_id"
deploy/scripts/verify-gpu-instance \
  --container "$container_id" --instance-id "$instance_id" \
  --physical-gpu 0 --process-name asr_offline --assert-stopped \
  --evidence "$RELEASE_ROOT/gpu-instances/asr-offline-gpu0.json" \
  --output "$RELEASE_ROOT/recovery/asr-offline-gpu0-stopped.json"
docker restart "$container_id"
deploy/scripts/verify-operator-registration \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --git-sha "$EXPECTED_GIT_SHA" \
  --reports-root "$REPORT_ROOT" --instance "$instance_id"
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
    --result-root /data/result \
    --callback-listen-host "$ALGORITHM_PLATFORM_GATEWAY" \
    --callback-advertise-base-url "http://${ALGORITHM_PLATFORM_GATEWAY}:19090" \
    --endpoints-json /root/workspace/.algorithm-scheduling-fixtures/v1.0_260812/endpoints.json \
    --operator "$operator_code" --instance "$instance_id" --run-id auto
done
```

六个 CPU 结果齐备后，确认 FaceRec 三个容器同时 running，并用一份独立的三实例
注册报告确认它们同时 `ONLINE` 且 `model_ready=true`：

```bash
for service_name in facerec-gpu0 facerec-gpu1 facerec-gpu2
do
  container_id="$(resolve_operator_container_id "$service_name")"
  test "$(docker inspect -f '{{.State.Running}}' "$container_id")" = true
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
  --result-root /data/result \
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
if ! operator_lifecycle_lock_is_held; then
  echo "阶段 6 拒绝在未持有 release-tag 级维护锁时恢复" >&2
  exit 1
fi
validate_operator_ledger_file "$BASELINE_OPERATOR_IDS"
validate_operator_ledger_file "$NEW_OPERATOR_IDS"
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
deploy/scripts/restore-existing-containers "$SNAPSHOT" "$PAUSED_LEDGER"
release_operator_lifecycle_lock
```

清理采用两遍处理：第一遍复用发布前的 `validate_operator_identity` 并排除 baseline；
只有整份 new ledger 全部通过后，第二遍才逐个执行 `docker stop`。任一身份不合规时，
不得停止其中任何容器。
阶段 6 的 restore 仍使用阶段 1 选定的唯一 `$SNAPSHOT`/`$PAUSED_LEDGER`；
previous 续跑不得把 active paused ledger 复制成另一份可变账本。release-tag 级锁在
该 restore 成功后显式关闭 holder 控制管道并回收子进程；之前任一阶段退出则由
`EXIT` trap 兜底释放。

清理后确认原 `ocr-v6-amd` 恢复到快照状态。平台与四类基础设施继续运行；不得 prune、
不得删除卷、不得删除 `/data/result`。whole-stack `down` 只允许出现在与本服务器隔离的
本地开发环境，不属于本场景。

最后先聚合当前 release 的 canonical 输入，再渲染 JSON/Markdown 汇总。aggregator 校验
完整注册、GPU、Smoke 和声明输入，展开 `negative/cases.json` 与 `load/cases.json` 中的
243 条声明，并以 write-once 方式生成 `summary/cases.json`；renderer 要求通过用例有
证据文件、未执行用例有中文原因、所有用例使用同一 release/SHA，并拒绝跨目录或包含
敏感 token：

```bash
.venv/bin/python scripts/aggregate_milestone_2b_cases.py \
  --release-root "$RELEASE_ROOT" \
  --operator-compose deploy/docker-compose.operators.yml \
  --smoke-manifest deploy/operator-smoke-cases.json \
  --report-plan deploy/milestone-2b-report-plan.json \
  --output "$RELEASE_ROOT/summary/cases.json"

report_status=0
if .venv/bin/python scripts/render_milestone_2b_report.py \
  --input "$RELEASE_ROOT/summary/cases.json" \
  --release-root "$RELEASE_ROOT" \
  --output-json "$RELEASE_ROOT/summary/report.json" \
  --output-markdown "$RELEASE_ROOT/summary/report.md"
then
  report_status=0
else
  report_status=$?
fi
set -e

case "$report_status" in
  0)
    report_overall_status="$(
      .venv/bin/python - "$RELEASE_ROOT/summary/report.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["overall_status"])
PY
    )"
    if [[ "$report_overall_status" != "通过" ]]; then
      echo "renderer 返回 0，但 overall_status 不是通过" >&2
      exit 1
    fi
    printf '里程碑 2B 验收通过：overall_status=%s\n' "$report_overall_status"
    ;;
  3)
    echo "报告已生成但验收未通过（renderer 返回码 3）" >&2
    exit 3
    ;;
  *)
    echo "报告输入校验或发布错误（renderer 返回码 $report_status）" >&2
    exit "$report_status"
    ;;
esac
```

返回码 `0` 且 `overall_status` 为“通过”才表示验收通过；返回码 `3` 表示报告已生成但
验收未通过，其他返回码表示校验或发布错误。生成报告不等于验收通过。终端只输出
`overall_status` 和返回码说明，不打印证据原文；证据摘要只保存在报告索引中。

## 2026-08-17 现场执行结果

本次以 release `v1.0_260812` 和部署 SHA
`7efac20cf980ee64ea78fe297af6dfdfb2df5b28` 完成阶段 1-6。先前“当前未执行声明”中
关于四平台、24 实例和八类 Smoke 尚未执行的描述，已由下列现场事实取代：

- 四个平台服务和 PostgreSQL、Kafka、Redis、MongoDB 全部 healthy。
- 24 个算子实例全部完成注册、首次心跳、`ONLINE` 和 `model_ready=true`。
- 18 个 GPU 实例均执行验证流程，15 个通过；FaceRec 三实例因 Harness
  默认调用镜像中不存在的 `python` 而失败。`python3` 直接 FastDeploy 探针确认
  `framework_gpu_available=true`，但旧 release 中的真实 FAIL 不修改。
- GPU 实例停止、CUDA PID 残留校验、重启和注册恢复动作全部执行。
- PPT Slice/Text Analysis 六个 CPU 实例 Smoke `6/6` 通过，八类 full Smoke `8/8`
  通过。PPT 使用约 55 分钟、454 MB 的真实 P 视频；FaceRec 验证了三实例共享
  MongoDB 的人物建立、识别、查询和清理。
- 验收后本轮 24 个算子容器已停止，原容器已恢复；八个平台/基础设施容器
  保持 healthy，GPU 无残留进程。本轮无 OOM、NVIDIA Xid、kernel OOM 或磁盘不足。

报告生成保留了部署证据和工具版本的区别：

- aggregator 工具使用后续修复提交
  `349f4a7673e1cc203661a11c422f30b4408a1073` 生成 write-once `summary/cases.json`。
- renderer 使用包含完整容器 ID 合同修复的提交
  `22a2d55f4523785e62cb384fb1a0ee3a6077d25e` 生成 `summary/report.json` 和
  `summary/report.md`。
- 三个报告文件的 SHA-256 分别为
  `4e75f1a657096adba74c9766f2ce24e3d1e69224c3ed1fc827e57e1706a9a877`、
  `8670fdc434e7e8ce19be1728743769928d7c8b699c1b1ce0791445b996b79fe7` 和
  `0aa03b2a524a38fe78e22e96ef2dab64343c076b343ae689154da2672af0d8ca`。

最终报告共 332 条用例：83 通过、6 失败、243 条“未执行及原因”。六条失败是
FaceRec 三条 GPU runtime 及对应的三条 recovery；217 条反例和 26 条压力用例仍未执行。
renderer 按预期返回 `3`，`overall_status=失败`。因此本次已完成部署与真实算子
直接 Smoke，但里程碑 2B 整体验收未通过；完整业务泳道也不在本次通过范围内。

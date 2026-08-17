# Single-project platform deployment

## 里程碑 2B 部署验证入口

完整顺序和证据边界见
[`../harness/scenarios/milestone-2b-deploy.md`](../harness/scenarios/milestone-2b-deploy.md)。
以下命令只适用于已通过服务器预检的 x86_64/三卡主机；MacBook 本地不得将 fake
GPU 或静态 Compose 结果当作真实部署通过。

服务器固定发布合同为 `root@192.168.29.11:22`，登录密码 `kedacom_123`，代码目录
`/root/workspace/algorithm-scheduling`。本次部署不创建或读取 `.env`；Git 中已批准保留
部署模板、该登录合同和受控服务默认值。模型解密密钥、SSH 私钥、课程媒体、人脸原图、
大型 fixture 和外部可信模型 manifest 仍必须留在 Git 工作树外。

发布必须先取得完整 commit SHA。服务器已经持有的
`/root/workspace/.algorithm-scheduling-assets/v1.0_260812/model-assets.manifest.json`
是外部可信基线；部署不得运行 `generate-model-asset-manifest` 重新生成或覆盖它，只能暂存并校验：

```bash
RELEASE_TAG=v1.0_260812
EXPECTED_GIT_SHA="$(git -C .. rev-parse HEAD)"
MODEL_ASSET_SOURCE=/root/workspace/.algorithm-scheduling-assets/v1.0_260812
RESTRICTED_REPORT_ROOT=/root/workspace/.algorithm-scheduling-restricted-reports

deploy/scripts/stage-model-assets \
  --source "$MODEL_ASSET_SOURCE" --workspace "$PWD/.."
deploy/scripts/verify-model-assets \
  --source "$MODEL_ASSET_SOURCE" --workspace "$PWD/.."
deploy/scripts/prepare-report-directory \
  --release-tag "$RELEASE_TAG" --git-sha "$EXPECTED_GIT_SHA" \
  --reports-root "$PWD/deploy/reports" \
  --restricted-root "$RESTRICTED_REPORT_ROOT" \
  --external-manifest "$MODEL_ASSET_SOURCE/model-assets.manifest.json"

EXPECTED_GIT_SHA="$EXPECTED_GIT_SHA" MODEL_ASSET_SOURCE="$MODEL_ASSET_SOURCE" \
  deploy/scripts/build-images v1.0_260812
```

模型源、可信 manifest、课程媒体、人脸原图、私钥和解密密钥均为外部受控输入，
不得提交到 Git 或写入报告。服务器登录密码是用户批准写入本文与 Git 的例外，不得把该
例外扩展到上述资产。报告根按
`deploy/reports/milestone-2b/releases/{release_tag}/{git_sha}/` 归档。

## Clean-clone Harness Python runtime

`.env` 是本次部署明确不使用的配置文件；`.venv` 是 Harness wrapper 需要的
Python 运行环境，两者不是同一个概念。canonical 里程碑 2B 始终准备并使用项目 `.venv`：

```bash
python3 -m venv "$PWD/.venv"
"$PWD/.venv/bin/python" -m pip install .
```

`run-operator-smoke` 和 `verify-operator-registration` wrapper 的解释器选择顺序为
`DEPLOY_PYTHON` -> 项目 `.venv/bin/python` -> `python3`。`preflight` 在项目 `.venv`
存在时也优先使用它。回退解释器仍必须自身具备 Harness 依赖；wrapper
只选择解释器，不会临时安装 `httpx`、PyYAML 或 `websockets`。因此系统
`python3` 回退只用于已事先准备同等依赖的非 canonical 环境；服务器 clean clone
必须遵循权威场景，在首次 preflight/Smoke 前创建 `.venv`，验证三个模块可
导入，并将 Python/依赖版本原子记录到当前 release 的 `preflight/`
证据中。完整可执行命令只以
[`milestone-2b-deploy.md`](../harness/scenarios/milestone-2b-deploy.md) 为准。

The platform Compose includes PostgreSQL, Kafka, Redis, MongoDB and all four platform
services under the single `algorithm-scheduling-platform` project. They communicate
over the explicitly named `algorithm-platform` network.

## Start the complete platform stack

```bash
docker compose -f deploy/docker-compose.platform.yml config --quiet
EXPECTED_GIT_SHA="$EXPECTED_GIT_SHA" \
  docker compose -f deploy/docker-compose.platform.yml up -d --build --wait --wait-timeout "${PLATFORM_WAIT_TIMEOUT_SECONDS:-180}"
docker compose -f deploy/docker-compose.platform.yml ps
deploy/scripts/preflight runtime --git-sha "$EXPECTED_GIT_SHA"
```

Do not start the infrastructure and platform files sequentially as separate Compose
projects. Use `docker-compose.infrastructure.yml` alone only for dependency tests or
when platform processes intentionally run on the host.

## Host endpoints

| Component | Host endpoint | Exposure / credentials |
|---|---|---|
| PostgreSQL | `127.0.0.1:5432` | database/user/password: `algorithm` |
| Kafka | `127.0.0.1:9092` | PLAINTEXT development listener |
| Redis | `127.0.0.1:6379` | database 0, no development password |
| MongoDB | `127.0.0.1:27017` | root username/password: `root`/`root`, `authSource=admin` |
| control-service | `0.0.0.0:18100` | A/可信内网北向入口 |
| orchestrator-service | `127.0.0.1:18101` | 仅宿主机运维/内部回调 |
| vision-orchestrator-service | `127.0.0.1:18102` | 仅宿主机运维 |
| online-gateway-service | `0.0.0.0:18103` | A/可信内网在线 HTTP/WebSocket 入口 |

These addresses are for host-run platform processes. Platform containers on the
`algorithm-platform` network use `postgres:5432`, `kafka:29092`, `redis:6379` and
`mongodb:27017`. FaceRec authenticates against MongoDB with `authSource=admin`.
Kafka 的固定双 listener 为
`KAFKA_LISTENERS=EXTERNAL://:9092,INTERNAL://:29092,CONTROLLER://:9093` 和
`KAFKA_ADVERTISED_LISTENERS=EXTERNAL://127.0.0.1:9092,INTERNAL://kafka:29092`；
不要在容器内使用 `127.0.0.1:9092`。

The MongoDB `root`/`root` values are approved committed defaults. They may be overridden
with shell environment variables `MONGO_ROOT_USERNAME` and `MONGO_ROOT_PASSWORD` before the first startup of an empty
`mongodb_data` volume. The operator Compose passes the same values to all three
FaceRec instances as `FACEREC_MONGO_USERNAME` and `FACEREC_MONGO_PASSWORD`; FaceRec
percent-encodes these separate fields when constructing its MongoDB URI, so reserved
URI characters in credentials are supported. Do not provide a prebuilt URI through
these variables. MongoDB initialization credentials apply only to an empty volume;
changing these environment variables does not rotate credentials stored in an existing
volume.

## Inspect logs and local-development stop

```bash
docker compose -f deploy/docker-compose.platform.yml logs -f postgres kafka redis mongodb
```

The canonical milestone 2B run never stops or brings down platform/infrastructure after
validation. A whole-stack `docker compose ... stop` is reserved for a separate local-development
environment and is forbidden in the 2B server scenario. Removing volumes is never part of the
normal workflow.

## Infrastructure-only dependency testing

For dependency tests that run platform processes on the host:

```bash
docker compose -f deploy/docker-compose.infrastructure.yml up -d
docker compose -f deploy/docker-compose.infrastructure.yml ps
```

Only `18100` for control and `18103` for the online gateway are remote northbound host
ports. `18101` for orchestrator and `18102` for vision are loopback-only. This Compose validates project layout, mounts,
dependency addresses and health checks. Until the background Kafka/Worker closure
Harness passes, healthy containers prove process deployment only, not complete DAG
execution.

For backup, ordered restart, operator drain, disk cleanup and single-machine recovery,
follow [单机运维与恢复手册](./单机运维与恢复手册.md).

For the northbound course/online contracts and deployment connectivity expected by
the upstream A service, follow [A服务接口与部署对接指南](./A服务接口与部署对接指南.md).

## Operator instances

`docker-compose.operators.yml` is the single-machine operator topology template. It
contains 24 instances. GPU 0, GPU 1 and GPU 2 each run one instance of all six GPU
operators: offline ASR, realtime ASR, OCR, VBas, face recognition and image quality.
The CPU profile runs three PPT Slice instances and three Text Analysis instances.

Every image used by this compose file must include the lightweight
`algorithm-operator-registry-client` distribution so that the operator can import
`packages.operator_registry_client`. Rebuild, validate and stage the exact wheel into
all eight operator build contexts with the repository pipeline:

```bash
python scripts/build_and_stage_operator_registry_wheel.py
python -m pip install \
  packages/operator_registry_client/dist/algorithm_operator_registry_client-0.1.0-py3-none-any.whl
```

All eight operator projects declare `algorithm-operator-registry-client==0.1.0` in
their runtime requirements. Their Dockerfiles consume the same versioned artifact
from an ignored `wheel/` build-context directory. The command above builds without an
index from a clean Git-tracked source allowlist, validates the fixed filename,
metadata, wheel member set and RECORD, atomically publishes `dist`, and stages
byte-identical copies with SHA-256 verification. A private cross-process lock and
durable transaction journal serialize the nine destination replacements and recover
an interrupted publication before the next run. Run it before building images.
The legacy command remains an alias for the same rebuild-and-stage pipeline and never
copies a previously built wheel without rebuilding:

```bash
python scripts/stage_operator_registry_wheel.py
```

The authoritative operator image matrix is `deploy/operator-images.tsv`. Validate
all eight Dockerfiles and build contexts without building an image:

```bash
deploy/scripts/verify-operator-build-contexts
```

The gate rejects matrix drift, workspace-root build contexts, `COPY`/`ADD` sources
outside an operator context and missing `.dockerignore` controls for Git state,
tests, caches, local Harness/OpenSpec/Codex data and common secret-file classes.
After model assets have been staged through the separately controlled asset process,
build and inspect all eight images from any working directory:

```bash
ASSET_SOURCE=/root/workspace/.algorithm-scheduling-assets/v1.0_260812
install -d -m 0700 "$ASSET_SOURCE"

# The existing model-assets.manifest.json is the externally supplied trusted baseline.
# Do not regenerate it during deployment. Transactionally publish all six model roots.
deploy/scripts/stage-model-assets \
  --source "$ASSET_SOURCE" --workspace "$PWD/.."

# Verify again immediately before a release build.
deploy/scripts/verify-model-assets \
  --source "$ASSET_SOURCE" --workspace "$PWD/.."

EXPECTED_GIT_SHA="$(git -C .. rev-parse HEAD)" \
MODEL_ASSET_SOURCE="$ASSET_SOURCE" \
deploy/scripts/build-images                    # default: v1.0_260812
```

Before collecting milestone 2B runtime evidence, initialize the release/SHA archive:

```bash
RELEASE_TAG=v1.0_260812
RELEASE_GIT_SHA="$(git -C .. rev-parse HEAD)"
RESTRICTED_REPORT_ROOT=/root/workspace/.algorithm-scheduling-restricted-reports

deploy/scripts/prepare-report-directory \
  --release-tag "$RELEASE_TAG" \
  --git-sha "$RELEASE_GIT_SHA" \
  --reports-root "$PWD/deploy/reports" \
  --restricted-root "$RESTRICTED_REPORT_ROOT" \
  --external-manifest "$ASSET_SOURCE/model-assets.manifest.json"
```

Normal evidence is written beneath
`deploy/reports/milestone-2b/releases/{release_tag}/{git_sha}` and is ignored by Git.
The external per-file model manifest is copied only to the Git-external restricted root.
Both roots use release tag and full commit SHA so evidence from different builds cannot be
silently combined. See `deploy/reports/README.md` for categories, permissions and the
redaction boundary.

### Final SHA and report gate

即使代码改动只影响 Harness，提交后得到的新最终 SHA 也会使旧镜像中的
`org.opencontainers.image.revision` 和旧 release evidence 对本轮验收失效。
四个平台和八个算子镜像必须按新最终 SHA 重新构建或重标，并重新取证；
不得把旧 SHA 的构建、注册、GPU 或 Smoke 证据复制到新 release 目录。重标后的运行镜像仍必须通过 runtime/operator
preflight 对最终 SHA 的 revision 校验。

报告聚合只接受 [`deploy/reports/README.md`](reports/README.md) 列出的 canonical 文件，
其中包括 `registration/operator-registration-profile-gpu0.json`、
`negative/cases.json` 和 `load/cases.json`。阶段 6 必须先运行 aggregator，再运行 renderer：

```bash
.venv/bin/python scripts/aggregate_milestone_2b_cases.py \
  --release-root "$RELEASE_ROOT" \
  --operator-compose deploy/docker-compose.operators.yml \
  --smoke-manifest deploy/operator-smoke-cases.json \
  --report-plan deploy/milestone-2b-report-plan.json \
  --output "$RELEASE_ROOT/summary/cases.json"

.venv/bin/python scripts/render_milestone_2b_report.py \
  --input "$RELEASE_ROOT/summary/cases.json" \
  --release-root "$RELEASE_ROOT" \
  --output-json "$RELEASE_ROOT/summary/report.json" \
  --output-markdown "$RELEASE_ROOT/summary/report.md"
```

renderer 返回码 `0` 表示报告结论通过；返回码 `3` 表示报告已生成但验收未通过；其他
非零返回码表示输入校验或发布错误。生成报告不等于验收通过，自动化必须保留并处理
返回码，不能只检查 `report.md` 是否存在。

The asset definition is `deploy/model-assets.json`: ASR Offline, ASR Online, OCR,
VBas plain models, FaceRec and ScreenDet. PPT Slice and Text Analysis have no local
model roots. The external manifest is an input artifact and must stay outside Git.
The external asset root must be owned by the execution user, contain no symlink in its
path and have exact `0700` mode; its pre-existing trusted manifest must have exact `0600`
mode. Report archival validates the same ownership, mode, worktree and no-follow
boundary before creating any release directories; it never repairs an insecure source.
The transaction rejects missing/extra files, path traversal, symlinks, special files,
secret/encrypted paths, nested `manifest.sha256`, duplicate JSON keys and hash drift.
Before creating any stage
directory it fsyncs a `preparing` journal containing all six exact stage/backup paths;
restart recovery removes only paths named by that transaction, rolls back interrupted
replacements and leaves unknown similarly named directories untouched. A private lock,
same-filesystem staging and fsync prevent a killed copy from accumulating partial roots
or publishing a mixed release.

`build-images` derives the OCR runtime `manifest.sha256` from the protected external
manifest into a temporary `0600` file outside the worktree. Only the OCR build receives
that file as a required BuildKit secret. The Dockerfile verifies the exact copied file
set and every digest before installing the derived runtime manifest, and the build entry
removes the temporary file on success, failure or interruption. The Git worktree and
external model roots do not gain a second manifest authority.

Before deployment, copy the two canonical endpoint files from `deploy/endpoints.json`
and `deploy/endpoints-full.json` into the Git-external fixture root. Per-instance GPU
triggers use the external `endpoints.json`; the full eight-operator Smoke command uses
the external `endpoints-full.json`.

Python wheels may be downloaded while each image is built. A quiet BuildKit log alone
is not a failure: continue while network bytes, Docker cache size or filesystem writes
show progress. Stop only for an explicit HTTP/connection failure or a sustained interval
with no byte-level progress; do not cancel a slow but advancing Torch/CUDA wheel download.

After a GPU operator is healthy, collect its evidence while the corresponding real
smoke request is still running. The trigger file is a JSON argv array and is executed
without a shell; command arguments are not copied into the report:

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
    echo "expected exactly one Compose container for ${service_name}" >&2
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
    echo "Compose container identity mismatch for ${service_name}" >&2
    return 1
  fi
  printf '%s\n' "$container_id"
}

service_name=asr-offline-gpu0
instance_id=asr-offline-gpu0
container_id="$(resolve_operator_container_id "$service_name")"

cat >/tmp/asr-offline-gpu0-trigger.json <<JSON
["/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/scripts/run-operator-smoke", "--release-tag", "${RELEASE_TAG}", "--git-sha", "${RELEASE_GIT_SHA}", "--reports-root", "/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/reports", "--fixture-manifest", "/root/workspace/.algorithm-scheduling-fixtures/v1.0_260812/manifest.json", "--external-fixture-root", "/root/workspace/.algorithm-scheduling-fixtures/v1.0_260812", "--fixture-target-root", "/data/course/_harness/fixtures", "--result-root", "/data/result", "--endpoints-json", "/root/workspace/.algorithm-scheduling-fixtures/v1.0_260812/endpoints.json", "--operator", "asr_offline", "--instance", "asr-offline-gpu0", "--run-id", "auto", "--repeat", "1", "--hold-seconds", "30"]
JSON

deploy/scripts/verify-gpu-instance \
  --container "$container_id" \
  --instance-id "$instance_id" \
  --physical-gpu 0 \
  --process-name asr_offline \
  --trigger-file /tmp/asr-offline-gpu0-trigger.json \
  --output "deploy/reports/milestone-2b/releases/${RELEASE_TAG}/${RELEASE_GIT_SHA}/gpu-instances/asr-offline-gpu0.json"
```

Do not replace the real Smoke trigger with `sleep` or a health request. A PASS requires
a target CUDA PID sampled while that trigger is issuing inference requests, an exact
full-container-ID cgroup mapping and a matching process name. After stopping the same
container, run the complete stopped check:

```bash
docker stop "$container_id"
deploy/scripts/verify-gpu-instance \
  --container "$container_id" \
  --instance-id "$instance_id" \
  --physical-gpu 0 \
  --process-name asr_offline \
  --assert-stopped \
  --evidence "deploy/reports/milestone-2b/releases/${RELEASE_TAG}/${RELEASE_GIT_SHA}/gpu-instances/asr-offline-gpu0.json" \
  --output "deploy/reports/milestone-2b/releases/${RELEASE_TAG}/${RELEASE_GIT_SHA}/recovery/asr-offline-gpu0-stopped.json"
docker restart "$container_id"
deploy/scripts/verify-operator-registration \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --git-sha "$RELEASE_GIT_SHA" \
  --reports-root "$PWD/deploy/reports" --instance "$instance_id"
```

每个 GPU 实例都必须执行同一顺序：真实推理采样、`docker stop`、立即
`--assert-stopped`、`docker restart`，然后等待注册、首次心跳、`ONLINE` 和
`model_ready=true`。只有实例恢复后才能验证下一个实例，最终必须让 24 个实例同时
ONLINE。恢复后使用 `verify-operator-registration --instance <当前实例>` 生成独立
write-once 报告，不要重复运行已生成报告的 profile preflight。不得为了收集停止证据而把容器留在停止状态。

Existing reports are never overwritten. Local fake-runtime tests prove verifier behavior
only; they do not prove that the target NVIDIA server or any operator passed GPU acceptance.

Current VBas and ScreenDet deployment uses plain models. Their encrypted directories
and keys are excluded from image contexts. If encrypted mode is introduced later, pass
the key via a separate read-only `/run/secrets/*` mount and do not include plain weights
in that encrypted image. `verify-runtime-secrets` opens every host-source path component
with no-follow semantics, rejects a symlink parent, and requires the direct configuration
directory to be owned by the current UID without group/other write permission. It then
validates only ID, container target, regular-file type, owner and exact `0600` mode; it
never reads or reports secret content, size or hashes. ASR Online's current `.enc`
implementation embeds its decryption material in source and is therefore an acknowledged
risk, not a secure secret boundary.

All eight runtime TOML files come from read-only Compose mounts under
`deploy/config/operators/`; local `config*.toml` files are excluded from images.
The build entrypoint verifies model assets, validates build inputs, stages the registry wheel,
checks root-disk free space
before the sequence and before every image, applies the fixed Git commit as the
`org.opencontainers.image.revision` label, and verifies both image reference and
revision through `docker image inspect`. It stops on the first failure and never
prunes containers, images, volumes or build cache.

Production automation may instead download that exact wheel from the internal
artifact repository into the build context. Images must not mount or add the platform
source tree to `PYTHONPATH`. After an internal Python package index is available, the
same exact requirement pin can be resolved from that index and the staging step can be
replaced by the release pipeline.

The infrastructure/platform Compose creates the shared `algorithm-platform` network.
After it is running, validate and start the operator topology:

```bash
docker compose -f deploy/docker-compose.operators.yml config --quiet
```

算子都属于显式 profile；不要用无 profile 的 `up` 作为启动命令。按下方
gpu0/gpu1/gpu2/cpu 顺序启动并在每一步执行 preflight。

The template uses these invariants:

- `restart: unless-stopped` lets Docker recover a failed process.
- `/data/course` and `/data/result` are shared host mounts.
- each endpoint has a unique `PLATFORM_INSTANCE_ID` and `PLATFORM_SERVICE_URL`.
- `PLATFORM_GPU_ID` records the routing label; `NVIDIA_VISIBLE_DEVICES` constrains the
  container to the same GPU.
- `/ops/health` checks process liveness after model startup.
- ASR always uses one Uvicorn worker per container; more capacity means more containers.

Override image tags, host data roots and optional capacity variables through the
environment before running Compose. Do not reuse an `instance_id` for two live
containers.

逐卡启动顺序必须在 canonical 场景从发布变量到阶段 6 的同一 Bash 会话中进行，不能
跳过阶段 1/2 后单独复制阶段 3。下列 `start_operator_profile` 在阶段 3 定义；不得把它
替换成直接的 Compose `up`，也不要用 `down -v`：
首次发布的 `PREVIOUS_RELEASE_ROOT` 为空；同 release tag 换 SHA 续跑时，必须在
canonical 发布变量块前显式把它设为上一 SHA 的绝对 release 目录，不自动
选择历史目录。

```bash
docker compose -f deploy/docker-compose.infrastructure.yml up -d
EXPECTED_GIT_SHA="$EXPECTED_GIT_SHA" \
  docker compose -f deploy/docker-compose.platform.yml up -d --build --wait --wait-timeout "${PLATFORM_WAIT_TIMEOUT_SECONDS:-180}"
deploy/scripts/preflight runtime --git-sha "$EXPECTED_GIT_SHA"
start_operator_profile gpu0
deploy/scripts/preflight operators --profile gpu0 --git-sha "$EXPECTED_GIT_SHA" \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --reports-root "$PWD/deploy/reports"
start_operator_profile gpu1
deploy/scripts/preflight operators --profile gpu1 --git-sha "$EXPECTED_GIT_SHA" \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --reports-root "$PWD/deploy/reports"
start_operator_profile gpu2
deploy/scripts/preflight operators --profile gpu2 --git-sha "$EXPECTED_GIT_SHA" \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --reports-root "$PWD/deploy/reports"
start_operator_profile cpu
deploy/scripts/preflight operators --profile cpu --git-sha "$EXPECTED_GIT_SHA" \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --reports-root "$PWD/deploy/reports"
deploy/scripts/preflight operators --full --git-sha "$EXPECTED_GIT_SHA" \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --reports-root "$PWD/deploy/reports"
```

`preflight runtime/operators` 对运行容器使用的最终镜像执行
`org.opencontainers.image.revision` attestation；`run-operator-smoke --git-sha` 只把结果
归档到对应 release/SHA，不证明镜像来源。FaceRec 的 gpu0/gpu1/gpu2 三实例必须同时
running/ONLINE 后再执行该算子的 Smoke。CPU profile 的
`ppt-slice-cpu0/1/2`、`text-analysis-cpu0/1/2` 必须使用逐实例
`endpoints.json` 分别执行 Smoke，不能只以 cpu0 代表六个实例。六个 CPU 结果齐备后，
先确认 FaceRec 三实例同时 running/ONLINE，最后只使用一次
`endpoints-full.json` 执行八类 full Smoke。

PPT Smoke 的 `19090` 是 Harness-only 临时回调端口，不属于平台北向暴露面。
必须动态读取 `algorithm-platform` Docker bridge gateway，并同时作为监听和广播
地址；不得绑定 `0.0.0.0` 或服务器物理网卡：

```bash
ALGORITHM_PLATFORM_GATEWAY="$(
  docker network inspect algorithm-platform \
    --format '{{(index .IPAM.Config 0).Gateway}}'
)"
test -n "$ALGORITHM_PLATFORM_GATEWAY"
test "$ALGORITHM_PLATFORM_GATEWAY" != "<no value>"

# 在六个 CPU 逐实例 Smoke 和唯一一次 full Smoke 中复用该参数数组。
CALLBACK_SMOKE_ARGS=(
  --callback-listen-host "$ALGORITHM_PLATFORM_GATEWAY"
  --callback-advertise-base-url "http://${ALGORITHM_PLATFORM_GATEWAY}:19090"
)
# run-operator-smoke 的完整命令见 milestone-2b-deploy.md，并展开 "${CALLBACK_SMOKE_ARGS[@]}"。
```

`run-operator-smoke` 只在 PPT 用例中启动监听，每次 Smoke 结束后立即关闭。

2B 清理不得对 platform/infrastructure 执行 `down`，也不得宽泛停止已有业务。执行前先
快照，并且只按同一 ledger 暂停用户明确允许的原 `ocr-v6-amd`。baseline、每次
current 快照和 new 差集都必须先写入 release `container-maintenance/` 内的同目录
`mktemp` 文件，完成 64 位容器 ID、`docker inspect .Id`、baseline 排除和 project/service
校验后才原子替换权威 ledger。完整可执行脚本只以
`harness/scenarios/milestone-2b-deploy.md` 为准，本 README 不复制第二套简化脚本。
每个 profile 都通过 canonical `start_operator_profile` 启动：无论 Compose 成功或失败，
都先基于 baseline 刷新原子 new ledger，再对 partial-up 返回原 Compose 退出码。
如果 ledger refresh 本身失败，保留已发布 baseline/new ledger，禁止执行 cleanup；
待 Docker 恢复后必须基于 baseline 重新刷新，不得用旧 new ledger 推断清理边界。
验收结束只停止经 Compose project `algorithm-operators` 和 service 标签复核的本轮新增
容器，不执行 `docker rm`；再用原 ledger 恢复 `ocr-v6-amd`。禁止 prune、`down -v`、
删除任何卷或删除 `/data/result`。

canonical 场景在任何 snapshot/pause 前获取同 release tag 共享的非阻塞
`flock`；锁路径以 `O_NOFOLLOW` 打开并校验 UID、`0600`、单链接和 inode，
holder 持有到阶段 6 restore 成功后才显式释放。同 SHA 已有完整本地
snapshot/paused 时复用原账本。指定 previous release 时，可从其直接账本或
provenance 继承原 authority；A→B→C 中 C 记录立即前驱 B，但 snapshot/paused
仍指向 A。当前 release 只以不可替换方式写入当前 UID 所有、权限
`0400` 的 provenance；同 SHA 续跑必须继续给出相同 immediate previous，
不得改绑或复制可变 paused ledger。

host preflight 在同一 tag 锁内执行。fresh 路径总是强制空
`AUTHORIZED_OCCUPIED_ENDPOINTS`；只有 same-/cross-SHA 续跑才从权威 platform 和
operator Compose 配置中按渲染的 service 精确查询 running 容器，验证完整
ID、project/service 标签和实际端口映射后，从 Docker inspect 的实际绑定派生
“监听地址+端口”授权集（例如 `127.0.0.1:18101`、`0.0.0.0:18100` 和
`[::]:18100`）。旧的纯数字端口授权不生效；preflight 对 `ss` 的每条必需端口监听
逐条精确匹配，因此同端口的其他地址或地址族占用仍 fail closed。

当前 release 的 baseline/new 必须成对存在；只有一份时 fail closed。两份均存在时
按同 SHA 恢复处理，保留 baseline 并只刷新 new。新 SHA 仍要求 previous root 属于同一
`REPORT_ROOT`/release tag 且 SHA 不同，但不得假定立即前驱一定持有算子账本。
阶段 3 的只读 resolver 从立即前驱开始：遇到最近的完整 baseline/new 对即返回；无账本时
只允许沿经过所有权、`0400`、schema、source SHA/root 和 authority 路径校验的 maintenance
provenance `source_release_root` 回溯。partial、环或最终没有完整账本祖先都 fail closed，
且不得改写当前或祖先 provenance。解析出的 baseline/new 仍必须通过排序、ID、inspect 和
Compose 身份校验；重算的 `current - resolved baseline` 必须与 resolved new 精确一致，
随后才原子继承 baseline 并立即刷新 new。Compose 同 service 换 ID 后仍按当前 ID 刷新，
不删除容器规避身份校验。

逐卡 Compose 后必须依次运行 `verify-gpu-instance`、`verify-operator-registration`
和 `run-operator-smoke`，最后用报告 renderer 生成 JSON/Markdown 汇总。只执行
health/readiness 不能证明模型推理、GPU 进程或课程泳道成功。

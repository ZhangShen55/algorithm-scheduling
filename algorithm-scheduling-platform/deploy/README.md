# Single-project platform deployment

## 里程碑 2B 部署验证入口

完整顺序和证据边界见
[`../harness/scenarios/milestone-2b-deploy.md`](../harness/scenarios/milestone-2b-deploy.md)。
以下命令只适用于已通过服务器预检的 x86_64/三卡主机；MacBook 本地不得将 fake
GPU 或静态 Compose 结果当作真实部署通过。

服务器固定发布必须先取得完整 commit SHA，并从 Git 工作树外提供模型资产：

```bash
RELEASE_TAG=v1.0_260812
EXPECTED_GIT_SHA="$(git -C .. rev-parse HEAD)"
MODEL_ASSET_SOURCE=/root/workspace/.algorithm-scheduling-assets/v1.0_260812
RESTRICTED_REPORT_ROOT=/root/workspace/.algorithm-scheduling-restricted-reports

deploy/scripts/generate-model-asset-manifest \
  --source "$MODEL_ASSET_SOURCE" --workspace "$PWD/.."
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

模型源、manifest、课程媒体、人脸原图、登录凭据、私钥和解密密钥均为外部
受控输入，不得提交到 Git 或写入报告。报告根按
`deploy/reports/milestone-2b/releases/{release_tag}/{git_sha}/` 归档。

The platform Compose includes PostgreSQL, Kafka, Redis, MongoDB and all four platform
services under the single `algorithm-scheduling-platform` project. They communicate
over the explicitly named `algorithm-platform` network.

## Start the complete platform stack

```bash
docker compose -f deploy/docker-compose.platform.yml config --quiet
docker compose -f deploy/docker-compose.platform.yml up -d --build
docker compose -f deploy/docker-compose.platform.yml ps
```

Do not start the infrastructure and platform files sequentially as separate Compose
projects. Use `docker-compose.infrastructure.yml` alone only for dependency tests or
when platform processes intentionally run on the host.

## Host endpoints

| Component | Host endpoint | Development credentials |
|---|---|---|
| PostgreSQL | `127.0.0.1:5432` | database/user/password: `algorithm` |
| Kafka | `127.0.0.1:9092` | PLAINTEXT development listener |
| Redis | `127.0.0.1:6379` | database 0, no development password |
| MongoDB | `127.0.0.1:27017` | root username/password: `root`/`root`, `authSource=admin` |

These addresses are for host-run platform processes. Platform containers on the
`algorithm-platform` network use `postgres:5432`, `kafka:29092`, `redis:6379` and
`mongodb:27017`. FaceRec authenticates against MongoDB with `authSource=admin`.
Kafka publishes separate host and Docker-network addresses; do not use
`127.0.0.1:9092` from inside a container.

The MongoDB `root`/`root` values are controlled test defaults. Override them with
`MONGO_ROOT_USERNAME` and `MONGO_ROOT_PASSWORD` before the first startup of an empty
`mongodb_data` volume. The operator Compose passes the same values to all three
FaceRec instances as `FACEREC_MONGO_USERNAME` and `FACEREC_MONGO_PASSWORD`; FaceRec
percent-encodes these separate fields when constructing its MongoDB URI, so reserved
URI characters in credentials are supported. Do not provide a prebuilt URI through
these variables. MongoDB initialization credentials apply only to an empty volume;
changing these environment variables does not rotate credentials stored in an existing
volume.

## Inspect logs and stop

```bash
docker compose -f deploy/docker-compose.platform.yml logs -f postgres kafka redis mongodb
docker compose -f deploy/docker-compose.platform.yml stop
```

Named volumes preserve local development data when containers stop. Removing volumes is intentionally not included in the normal workflow.

## Infrastructure-only dependency testing

For dependency tests that run platform processes on the host:

```bash
docker compose -f deploy/docker-compose.infrastructure.yml up -d
docker compose -f deploy/docker-compose.infrastructure.yml ps
```

Host ports are `18100` for control, `18101` for orchestrator, `18102` for vision and
`18103` for the online gateway. This Compose validates project layout, mounts,
dependency addresses and health checks. Until the background Kafka/Worker closure
Harness passes, healthy containers prove process deployment only, not complete DAG
execution.

For backup, ordered restart, operator drain, disk cleanup and single-machine recovery,
follow [单机运维与恢复手册](./单机运维与恢复手册.md).

For the northbound course/online contracts and deployment connectivity expected by
the upstream A service, follow [A服务接口与部署对接指南](./A服务接口与部署对接指南.md).

## Operator instances

`docker-compose.operators.yml` is the single-machine operator topology template. It
contains two independent offline ASR and two independent realtime ASR endpoints for
GPU 0/GPU 1, plus PPT slicing, OCR, text analysis, VBas, face recognition and image
quality instances.

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

# Run once after controlled SCP creates or changes the external source.
deploy/scripts/generate-model-asset-manifest \
  --source "$ASSET_SOURCE" --workspace "$PWD/.."

# Transactionally publish all six model roots into their build contexts.
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

The asset definition is `deploy/model-assets.json`: ASR Offline, ASR Online, OCR,
VBas plain models, FaceRec and ScreenDet. PPT Slice and Text Analysis have no local
model roots. The external manifest is an input artifact and must stay outside Git.
The external asset root must be owned by the execution user, contain no symlink in its
path and have exact `0700` mode. The generator atomically creates the manifest with exact
`0600` mode. Report archival validates the same ownership, mode, worktree and no-follow
boundary before creating any release directories; it never repairs an insecure source.
The transaction rejects missing/extra files, path traversal, symlinks, special files,
secret/encrypted paths, duplicate JSON keys and hash drift. Before creating any stage
directory it fsyncs a `preparing` journal containing all six exact stage/backup paths;
restart recovery removes only paths named by that transaction, rolls back interrupted
replacements and leaves unknown similarly named directories untouched. A private lock,
same-filesystem staging and fsync prevent a killed copy from accumulating partial roots
or publishing a mixed release.

After a GPU operator is healthy, collect its evidence while the corresponding real
smoke request is still running. The trigger file is a JSON argv array and is executed
without a shell; command arguments are not copied into the report:

```bash
cat >/tmp/asr-offline-gpu0-trigger.json <<'JSON'
["/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/scripts/run-operator-smoke", "--operator", "asr_offline", "--instance", "asr-offline-gpu0"]
JSON

deploy/scripts/verify-gpu-instance \
  --container asr-offline-gpu0 \
  --physical-gpu 0 \
  --process-name asr_offline \
  --trigger-file /tmp/asr-offline-gpu0-trigger.json \
  --output "deploy/reports/milestone-2b/releases/${RELEASE_TAG}/${RELEASE_GIT_SHA}/gpu-instances/asr-offline-gpu0.json"
```

`run-operator-smoke` is delivered by the later smoke Harness task; do not replace it
with `sleep` or a health request. A PASS requires a target CUDA PID sampled while that
real trigger is alive, an exact full-container-ID cgroup mapping and a matching process
name. After stopping the same container, use `--assert-stopped --evidence <prior-json>`
and write the new report under the release `recovery/` directory. Existing reports are
never overwritten. Local fake-runtime tests prove verifier behavior only; they do not
prove that the target NVIDIA server or any operator passed GPU acceptance.

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
docker compose -f deploy/docker-compose.operators.yml up -d
```

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

逐卡启动顺序（真实验证时使用，不要用 `down -v`）：

```bash
docker compose -f deploy/docker-compose.infrastructure.yml up -d
docker compose -f deploy/docker-compose.platform.yml up -d --build
docker compose -f deploy/docker-compose.operators.yml --profile gpu0 up -d
docker compose -f deploy/docker-compose.operators.yml --profile gpu1 up -d
docker compose -f deploy/docker-compose.operators.yml --profile gpu2 up -d
docker compose -f deploy/docker-compose.operators.yml --profile cpu up -d
```

逐卡 Compose 后必须依次运行 `verify-gpu-instance`、`verify-operator-registration`
和 `run-operator-smoke`，最后用报告 renderer 生成 JSON/Markdown 汇总。只执行
health/readiness 不能证明模型推理、GPU 进程或课程泳道成功。

# Local infrastructure

The first development stage runs PostgreSQL, Kafka and Redis in three Docker containers. Platform processes run on the Mac host and use mapped localhost ports.

## Start

```bash
docker compose -f deploy/docker-compose.infrastructure.yml up -d
docker compose -f deploy/docker-compose.infrastructure.yml ps
```

## Host endpoints

| Component | Host endpoint | Development credentials |
|---|---|---|
| PostgreSQL | `127.0.0.1:5432` | database/user/password: `algorithm` |
| Kafka | `127.0.0.1:9092` | PLAINTEXT development listener |
| Redis | `127.0.0.1:6379` | database 0, no development password |

These addresses are for host-run platform processes. Platform containers on the
`algorithm-platform` network use `postgres:5432`, `kafka:29092` and `redis:6379`.
Kafka publishes separate host and Docker-network addresses; do not use
`127.0.0.1:9092` from inside a container.

## Inspect logs and stop

```bash
docker compose -f deploy/docker-compose.infrastructure.yml logs -f postgres kafka redis
docker compose -f deploy/docker-compose.infrastructure.yml stop
```

Named volumes preserve local development data when containers stop. Removing volumes is intentionally not included in the normal workflow.

## Four-service platform stack

Validate and start infrastructure plus all four platform service containers with:

```bash
docker compose -f deploy/docker-compose.platform.yml config --quiet
docker compose -f deploy/docker-compose.platform.yml up -d --build
docker compose -f deploy/docker-compose.platform.yml ps
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
`packages.operator_registry_client`. Build it once and install the wheel into local
operator environments:

```bash
cd packages/operator_registry_client
python -m pip wheel --no-deps --wheel-dir dist .
python -m pip install dist/algorithm_operator_registry_client-0.1.0-py3-none-any.whl
```

All eight operator projects declare `algorithm-operator-registry-client==0.1.0` in
their runtime requirements. Their Dockerfiles consume the same versioned artifact
from an ignored `wheel/` build-context directory. Stage the generated wheel into all
operator projects before building:

```bash
python scripts/stage_operator_registry_wheel.py
```

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

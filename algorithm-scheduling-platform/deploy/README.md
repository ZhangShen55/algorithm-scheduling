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

Every image used by this compose file must include the
`algorithm-scheduling-platform` Python distribution so that the operator can import
`packages.operator_registry_client`. During local development install it into the
operator environment from this repository:

```bash
python -m pip install -e /absolute/path/to/algorithm-scheduling-platform
```

Production images should install a versioned wheel built from this repository through
the internal artifact repository. They must not mount or add the platform source tree
to `PYTHONPATH`.

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

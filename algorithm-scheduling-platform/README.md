# Algorithm Scheduling Platform

工作区采用单仓库管理。四个可部署服务位于工作区根目录，本目录只保留公共包、
数据库迁移、部署编排、平台契约测试和 Harness。

Current maturity: the four FastAPI service projects, typed configuration, control-plane
components, PPT shared-result components and single-machine Compose are present. The
real Kafka-backed orchestrator and visual Worker loops are still being implemented;
the repository must not yet be treated as an end-to-end production runtime.

## Services

- `../control_service`: course task API, persistent status, Outbox and operator control plane.
- `../orchestrator_service`: offline Outbox publisher, Kafka consumers and general DAG execution.
- `../vision_orchestrator_service`: adaptive T/S frame analysis and aggregation.
- `../online_gateway_service`: request-level online image routing and realtime ASR session proxy.

## Shared packages

- `platform_common`: configuration, logging, trace and runtime helpers.
- `platform_contracts`: stable API, event and status contracts.
- `operator_registry_client`: active registration and heartbeat client for operators.

## Single-machine platform stack

```bash
docker compose -f deploy/docker-compose.platform.yml config --quiet
docker compose -f deploy/docker-compose.platform.yml up -d --build
```

The stack exposes control/orchestrator on `18100`/`18101`, and maps vision/online to
host ports `18102`/`18103`. All platform and infrastructure services share the
`algorithm-scheduling-platform` Compose project and the `algorithm-platform` network.
Host-run processes use PostgreSQL, Kafka, Redis
and MongoDB on localhost; containers use `postgres:5432`, `kafka:29092`, `redis:6379`
and `mongodb:27017`. Use the infrastructure Compose alone only for dependency tests;
do not start it and the platform Compose sequentially as separate projects.

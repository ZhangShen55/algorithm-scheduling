# Algorithm Scheduling Platform

工作区采用单仓库管理。四个可部署服务位于工作区根目录，本目录只保留公共包、
数据库迁移、部署编排、平台契约测试和 Harness。

Current maturity: the four FastAPI service projects, typed configuration, control-plane
components, PPT shared-result components, Kafka-backed orchestrator and visual Worker
runtime wiring, and single-machine Compose are present. Final seven-operator deployment,
real business-lane and recovery evidence is still incomplete, so the repository must not yet
be treated as an end-to-end production runtime.

The current scheduling topology contains seven operator types and 21 instances: 18 GPU
instances (six operator types on each of three GPUs) and three CPU PPT Slice instances.
`text_analysis/` remains in the workspace as a non-platform project; platform build, deployment,
registration, routing, leasing and verification must exclude it. Historical task and audit data
that mention Text Analysis remain readable.

New offline tasks use only these DAGs:

- PPT: `PPT_SLICE -> PPT_OCR`
- ASR: `ASR_TRANSCRIPTION`

Historical `PPT_KEYWORDS` and `COURSE_OVERVIEW` nodes may still appear in queries for old tasks,
but new tasks do not create placeholders for them.

七算子、四平台、四中间件和三 GPU 的首次部署、升级、回滚、常驻启停及精确清理，统一以
[算法功能调度平台部署手册](deploy/算法功能调度平台部署手册.md) 为中文操作权威。本 README
只说明仓库边界和开发入口，不再维护第二套生产部署顺序。

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
deploy/scripts/apply-course-task-submission-migration
docker compose -f deploy/docker-compose.platform.yml up -d --build --wait --wait-timeout "${PLATFORM_WAIT_TIMEOUT_SECONDS:-180}"
```

The stack exposes control/orchestrator on `18100`/`18101`, and maps vision/online to
host ports `18102`/`18103`. All platform and infrastructure services share the
`algorithm-scheduling-platform` Compose project and the `algorithm-platform` network.
Host-run processes use PostgreSQL, Kafka, Redis
and MongoDB on localhost; containers use `postgres:5432`, `kafka:29092`, `redis:6379`
and `mongodb:27017`. Use the infrastructure Compose alone only for dependency tests;
do not start it and the platform Compose sequentially as separate projects.

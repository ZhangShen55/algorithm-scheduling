# Algorithm Scheduling Platform Guide

This file governs the shared packages, migrations, deployment definitions, cross-service tests and Harness under `algorithm-scheduling-platform/`. The four deployable service projects are siblings at the workspace root; the service boundaries below apply to those root projects as durable architecture rules.

## Service Boundaries

| Service | Owns | Must not own |
| --- | --- | --- |
| `control-service` | A-facing course APIs, PostgreSQL task facts/Outbox, Redis registry, lifecycle and leases | media download, model calls, Kafka consumption |
| `orchestrator-service` | Outbox publication, offline DAG, media preparation, general node execution, PPT terminal callback | online requests, adaptive visual decisions |
| `vision-orchestrator-service` | offline T/S extraction, adaptive VBas rounds, aggregation and evidence | RTSP online ingestion, operator registration authority |
| `online-gateway-service` | online Base64 request routing and realtime ASR WebSocket stickiness | Kafka, offline task creation, video download |

Keep the four services as separate processes and containers. `control-service` and `orchestrator-service` are both required for offline execution; visual and online services remain independently optional.

## Stable Contracts

- Preserve A fields `task_id`, `task_types`, `teacher_video_path`, `student_video_path`, `slides_video_path`, `front_points`, `back_point`, `student_count` and `asr_options` exactly.
- Preserve the four task types `PPT`, `ASR`, `TEACHER_BEHAVIOR` and `STUDENT_BEHAVIOR` and integer node states.
- Preserve operator code `vbas`; never reintroduce `tias` into new platform contracts.
- Online image requests contain upstream Base64 images. Do not add stream ingestion or frame extraction to the online gateway.
- PPT is an approved breaking internal contract: shared files under `/data/result/{task_id}/ppt`, atomic manifest, one terminal callback, no Base64 slide callbacks.
- PPT submission uses canonical `video_path`. Orchestrator emits the prepared absolute local path; the operator also accepts remote URLs and only keeps legacy `uri` as a compatibility input.
- Kafka messages contain identifiers, paths and metadata only, never media bytes.

## Dependency Ownership

- PostgreSQL is the durable authority for tasks, nodes, results, Outbox and audit facts.
- Redis is the live authority for operator TTL, lifecycle and atomic renewable leases; only control-service connects directly.
- Kafka carries course-level commands and visual events; online traffic never enters Kafka.
- `/data/course/{task_id}` is temporary. `/data/result/{task_id}` is durable and must survive terminal cleanup.
- `enabled_task_types` declares delivery support. Missing registered capacity causes status 30 and readiness detail, not dynamic removal of supported task types.

## Layout And Runtime

Each service owns `app/`, `tests/`, `docker/Dockerfile`, `config.toml`, `requirements.txt` and `README.md`. TOML fields require adjacent Chinese comments. Resolve config from service defaults, then `config.toml`, then environment variables. Production secrets belong in environment variables.

Run orchestrator and vision with one Uvicorn worker because lifespan starts background loops. Initial control and online deployments also use one worker; scale by containers after broker-backed evidence exists.

## Prohibited Shortcuts

- Do not call repository completion methods from end-to-end tests to simulate Worker output.
- Do not mark a runtime task complete because classes or health-only entrypoints exist.
- Do not let orchestrator, vision or online read the Redis registry directly; use control-service leases.
- Do not release an accepted asynchronous PPT lease before terminal persistence; renew it while running.
- Do not delete `/data/result/{task_id}` during normal cleanup.

## Verification Tiers

1. Static: compile/import, config parsing and route contracts.
2. Unit: state machine, adapters, manifest validation and aggregation.
3. Database/Redis integration: real PostgreSQL/Redis behavior.
4. Broker integration: real Kafka publish, consume, commit and recovery.
5. Service runtime: lifespan loops, readiness and shutdown.
6. Operator contract: HTTP/WebSocket calls through real leases.

Claims must name their achieved tier. Run commands from `harness/verification.md` and update Harness evidence whenever runtime wiring, deployment or contracts change.

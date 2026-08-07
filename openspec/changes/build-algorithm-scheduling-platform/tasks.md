## 1. 平台仓库与本地基础设施

- [x] 1.1 Create the `algorithm-scheduling-platform` monorepo with four service packages and three shared packages from the approved layout
- [x] 1.2 Define typed configuration loading, structured logging, trace context, error envelopes, and shared status constants
- [x] 1.3 Add local Docker Compose definitions for PostgreSQL, Kafka, and Redis with persistent volumes and documented host ports
- [x] 1.4 Add shared `/data/course` and `/data/result` directory configuration plus startup permission checks
- [x] 1.5 Add CI commands for linting, type checks, unit tests, contract tests, and database migration validation

## 2. 数据库基础

- [x] 2.1 Design the PostgreSQL logical schema for courses, task types, nodes, result metadata, Outbox, operator audit data, and visual fallback values
- [x] 2.2 Add initial migrations with integer status constraints and uniqueness for `(task_id, task_type)`
- [x] 2.3 Implement transactional repository methods for sparse task creation, idempotent lookup, node state updates, and structured result persistence
- [x] 2.4 Add indexes and concurrency tests for ready-node claiming, task querying, and Outbox scanning

## 3. control-service 课程接口

- [x] 3.1 Implement `POST /api/course-jobs` with `task_types`-scoped validation and the HTTP 200 business response envelope
- [x] 3.2 Implement request merging for ASR defaults and persistence of `effective_params`
- [x] 3.3 Implement idempotent behavior for completed, active, and newly appended `(task_id, task_type)` pipelines
- [x] 3.4 Implement `GET /api/course-jobs/{task_id}` returning all four task types, nodes, status text, Chinese reasons, file metadata, and structured results
- [x] 3.5 Add API tests for PPT-only, ASR-only, combined teacher tasks, student region inputs, duplicate submissions, and missing selected inputs

## 4. Outbox 与核心编排

- [x] 4.1 Implement transactional Outbox creation inside the course submission transaction
- [x] 4.2 Implement a non-blocking Outbox Publisher loop with publish confirmation, idempotency key, and metrics
- [x] 4.3 Implement Kafka consumers that idempotently initialize only the requested pipelines
- [x] 4.4 Implement integer node state transitions and prerequisite release rules
- [x] 4.5 Implement `URGENT` and `NORMAL` non-preemptive ready-node selection with FIFO ordering inside each priority
- [x] 4.6 Add restart, duplicate-event, concurrent-claim, unavailable-operator, and priority-order tests

## 5. 算子注册与容量路由

- [x] 5.1 Implement register, heartbeat, unregister, list, lease, and release APIs in `control-service`
- [x] 5.2 Implement Redis TTL heartbeat state and atomic capacity lease scripts with expiry recovery
- [x] 5.3 Implement ONLINE, DRAINING, and OFFLINE selection rules plus operations queries
- [x] 5.4 Build `operator_registry_client` for operator startup registration, heartbeat, drain, and shutdown unregister
- [x] 5.5 Add `/ops/health`, `/ops/status`, and `/ops/drain` integration requirements and contract tests
- [x] 5.6 Migrate all platform identifiers and tests to `vbas` and reject legacy `tias` registration codes

## 6. 媒体工作区与 PPT 管道

- [x] 6.1 Implement safe URL download and media metadata inspection into `/data/course/{task_id}`
- [x] 6.2 Implement shared-download reuse when multiple task types in one submission require the same teacher video
- [x] 6.3 Implement the PPT slice adapter and publish durable images under `/data/result/{task_id}/ppt/slices`
- [x] 6.4 Generate stable `ppt_image_id` values and dynamic OCR work with configurable batch/concurrency limits
- [x] 6.5 Implement per-slide OCR and `/v1/extract_keywords` calls with structured result persistence and progress counts
- [x] 6.6 Add end-to-end tests for slice-only completion visibility, delayed OCR availability, and per-slide keyword identity

## 7. ASR 与课程脑图管道

- [x] 7.1 Implement teacher-video audio extraction to a compatible local WAV file
- [x] 7.2 Implement the offline ASR v1.1.8 adapter with explicit effective parameters and business-error-body detection
- [x] 7.3 Persist and return the full successful ASR response without schema replacement
- [x] 7.4 Convert ASR segments into the existing `/v1/course_overviews` request contract
- [x] 7.5 Persist and return the complete course overview GenericResponse including model, id, nested result, completion metadata, and usage
- [x] 7.6 Add large-result, default-options, partial-option override, completed-result reuse, and silent/error ASR tests

## 8. vision-orchestrator-service

- [x] 8.1 Adapt `jy-vision-orchestrator-server` to consume course-level Kafka visual commands and publish progress/completion events
- [x] 8.2 Replace legacy instance scheduling and database assumptions with platform registry leases and PostgreSQL-owned results
- [x] 8.3 Implement configurable coarse scan, candidate grouping, bidirectional expansion, topology scan, and 10/5/2/1-second boundary refinement
- [x] 8.4 Implement frame and inference caching keyed by task, stream, timestamp, capability, model version, and ROI version
- [x] 8.5 Implement configurable VBas batch size/concurrency and synchronous capacity-aware HTTP calls
- [x] 8.6 Implement writing and sitting gap merge rules, half-open intervals, empty-behavior completion, and insufficient-valid-frame reasons
- [x] 8.7 Implement student attendance, stable-person, front/back ratio calculation, one-time configured fallback values, and provided flags
- [x] 8.8 Preserve existing five visual snapshot categories and add representative writing, sitting, and teaching images under `/data/result/{task_id}/vision`
- [x] 8.9 Add deterministic simulation and integration tests for refinement boundaries, conflicting frames, gap merging, no behavior, no valid teacher, and missing regions

## 9. online-gateway-service

- [x] 9.1 Implement `/api/online/vbas/analyze` as a Base64 request-level capacity-routed proxy
- [x] 9.2 Implement `/api/online/face/recognize` as a Base64 request-level capacity-routed proxy
- [x] 9.3 Implement `/api/online/image-quality/detect` using the operator `detect_all` contract
- [x] 9.4 Preserve complete-request routing and item-level partial success without cross-instance splitting
- [x] 9.5 Implement realtime ASR WebSocket connection routing, sticky capacity lease, bidirectional proxy, and disconnect cleanup
- [x] 9.6 Add concurrency, no-capacity, operator-disconnect, multi-image compatibility, and WebSocket stickiness tests

## 10. 算子部署迁移

- [x] 10.1 Add registry client integration to `asr_offline`, `asr_online`, `ppt_slice`, `ocr`, `text_analysis`, `vbas`, `facerec`, and `screen_det`
- [x] 10.2 Configure offline and online ASR as one worker per endpoint with separate ports and GPU labels, removing internal Nginx/multi-worker assumptions
- [x] 10.3 Register only `course_overviews` and `extract_keywords` as scheduling capabilities for `text_analysis`
- [x] 10.4 Verify each operator keeps its existing inference path, request, response, model loading, and default port behavior
- [x] 10.5 Add Docker restart policies, shared mounts, health checks, and per-instance environment configuration

## 11. 结果生命周期、可观测性与运维

- [x] 11.1 Implement node response mapping that separates `path/count` file artifacts from structured `result` values
- [x] 11.2 Implement terminal cleanup that removes only `/data/course/{task_id}` after durable results are confirmed
- [x] 11.3 Add task, node, Outbox, Kafka lag, operator availability, lease, latency, error, GPU label, and disk usage metrics
- [x] 11.4 Add structured audit logs linking `task_id`, task type, node, attempt, trace, instance, model version, and elapsed time
- [x] 11.5 Add operations APIs for course state, node state, registered instances, DRAINING, queues, and storage usage
- [x] 11.6 Document backup, restart, disk cleanup, operator drain, and single-machine recovery procedures

## 12. 端到端验收与 A 服务交接

- [x] 12.1 Run PPT-only, ASR-only, teacher-only, student-only, and combined-request end-to-end acceptance flows
- [x] 12.2 Verify completed task-type reuse and later task-type append behavior for the same `task_id`
- [x] 12.3 Verify URGENT insertion, waiting-operator visibility, process restart recovery, and Outbox delivery
- [x] 12.4 Verify online image requests never pull streams or enter Kafka and realtime ASR does not replace offline ASR
- [x] 12.5 Verify all local paths, result structures, ASR effective parameters, course overview nesting, and visual empty intervals match the documented contract
- [x] 12.6 Publish A-service API examples, business codes, field requirements by task type, and deployment connectivity guidance

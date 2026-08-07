## 1. Harness 与持久化 Agent 指南

- [x] 1.1 Add `algorithm-scheduling-platform` to the root `AGENTS.md` project map and document the cross-project rule that VBas remains frame inference only
- [x] 1.2 Create `algorithm-scheduling-platform/AGENTS.md` with four-service ownership, stable A/operator contracts, exact field names, dependency ownership, prohibited shortcuts, and required verification tiers
- [x] 1.3 Create `harness/README.md`, `architecture-review.md`, `change-ledger.md`, `verification.md`, and scenario templates with the evidence fields defined in the design
- [x] 1.4 Record the current audit baseline in Harness, including health-only Worker entrypoints, synthetic acceptance completion, absent Kafka adapter, unwired cleanup/metrics/audit, missing platform Compose, and missing operator wheel installation
- [x] 1.5 Add a Harness consistency test that verifies every architecture decision has an owner, evidence command, verdict, and linked scenario

## 2. 运行时配置与 Kafka 基础

- [x] 2.1 Add per-service annotated `config.toml`, typed settings for infrastructure addresses, enabled task types, topics/groups, poll limits, Worker concurrency, shutdown timeout, media limits, PPT shared output, lease renewal, and readiness probes
- [ ] 2.2 Select and add a production Kafka client after verifying wheel compatibility with the target Python and operator environments; record the decision in Harness
- [ ] 2.3 Implement shared async Kafka producer and consumer adapters with start, stop, send confirmation, manual commit, bounded polling, and lag metrics
- [ ] 2.4 Add topic bootstrap/validation for `algorithm.course.commands`, `algorithm.visual.commands`, and `algorithm.visual.events`
- [ ] 2.5 Add real-broker tests for publish, consume, manual commit, reconnect, duplicate delivery, and unavailable-broker readiness

## 3. orchestrator-service 运行时闭环

- [ ] 3.1 Replace the health-only orchestrator entrypoint with a lifespan-managed runtime factory that owns engine, HTTP client, Kafka resources, stop event, and background task group
- [ ] 3.2 Wire Outbox Publisher to the real Kafka producer and verify failed publication remains pending until broker recovery
- [ ] 3.3 Wire the course-command consumer to `PipelineInitializer` with successful-processing offset commits and idempotent duplicate consumption
- [ ] 3.4 Implement the capacity client/dispatcher loop that claims URGENT before NORMAL without preempting running nodes and exposes status 30 when capacity is unavailable
- [ ] 3.5 Implement execution context and shared-download coordination keyed by `submission_id` so combined ASR/teacher work shares T only within one submission
- [ ] 3.6 Replace the internal PPT operator contract with shared-path slice output, atomic manifest publication, one idempotent terminal callback, manifest reconciliation, renewable capacity lease, and OCR release after durable validation
- [ ] 3.7 Implement `PPT_OCR` and `PPT_KEYWORDS` execution using per-`ppt_image_id` work items, configured concurrency, leases, and partial progress persistence
- [ ] 3.8 Implement `ASR_TRANSCRIPTION` media/WAV/lease execution with v1.1.8 business-body validation and complete response persistence
- [ ] 3.9 Implement `COURSE_OVERVIEW` execution from stored ASR segments and persist the complete nested GenericResponse
- [ ] 3.10 Implement teacher/student visual node dispatch to `algorithm.visual.commands` and consume visual progress/completion events idempotently
- [ ] 3.11 Implement task-type status derivation and current-node reasons from node states without test-side or operator-side direct task updates
- [ ] 3.12 Add orchestrator readiness and shutdown tests proving required loops start, fail visibly, stop consumption, and close resources

## 4. vision-orchestrator-service 运行时闭环

- [ ] 4.1 Replace the health-only vision entrypoint with a lifespan-managed course-level visual consumer and progress/completion producer
- [ ] 4.2 Implement safe local T/S frame extraction with timestamp identity, configurable coarse/refinement plans, limits, and `/data/course/{task_id}` ownership
- [ ] 4.3 Implement a concrete `VisualAnalyzer` composing cache, `AdaptiveScanPlanner`, capacity-routed `VbasBatchClient`, teacher interval aggregation, student aggregation, and progress callbacks
- [ ] 4.4 Integrate writing/sitting gap tolerance, insufficient-valid-frame reasons, empty completed intervals, front/back provided flags, and stable PostgreSQL fallback values
- [ ] 4.5 Integrate selected evidence publication under `/data/result/{task_id}/vision` while ordinary frames remain temporary
- [ ] 4.6 Add deterministic analyzer integration tests with an HTTP VBas contract server for teacher, student, refinement, conflicting frames, no behavior, and insufficient imagery
- [ ] 4.7 Add real Kafka command/progress/completion tests and restart/idempotency tests for long visual tasks

## 5. 注册审计、清理与可观测性

- [ ] 5.1 Add PostgreSQL repository methods for operator registration facts, heartbeat summaries, lifecycle changes, unregister events, and operations history
- [ ] 5.2 Wrap Redis registry operations so current TTL/lease state remains in Redis while durable facts are transactionally recorded in PostgreSQL
- [ ] 5.3 Wire task/node state gauges, Kafka lag, operator readiness, active leases, Outbox backlog, latency, errors, GPU labels, and disk usage to actual runtime snapshots
- [ ] 5.4 Wire structured node audit logs at claim/start/operator-result/failure/completion boundaries with task, node, attempt, trace, instance, model, elapsed time, and outcome
- [ ] 5.5 Invoke `TerminalWorkspaceCleaner` from task finalization and record success/deferred/error outcomes without deleting `/data/result/{task_id}`
- [ ] 5.6 Fix `/ops/queues` metrics to label actual node codes rather than capability names and add operations snapshot tests
- [ ] 5.7 Close online-gateway shared HTTP resources in lifespan and add shutdown regression coverage

## 6. 可复现的单机部署

- [x] 6.1 Restructure all four platform services as complete FastAPI projects with per-service `app` packages, annotated `config.toml`, `requirements.txt`, placeholder-first Dockerfiles, then add a validated Compose definition with restart, readiness, shared mounts, resource configuration, and the common network
- [x] 6.2 Add Kafka host/internal listeners and document which bootstrap address applies to host processes versus containers
- [ ] 6.3 Build a versioned platform wheel and update all eight operator image builds to install it without source mounts or ad hoc `PYTHONPATH`
- [ ] 6.4 Add isolated image tests that import `packages.operator_registry_client`, start each operator with registration enabled, and preserve its business routes/default port
- [ ] 6.5 Add deployment preflight for writable `/data/course`, durable `/data/result`, GPU labels, unique instance IDs, database migrations, topics, and required ports

## 7. 证据级端到端验收

- [ ] 7.1 Build contract-compatible HTTP/WebSocket operator stubs for PPT slice callbacks, OCR, text analysis, ASR offline/online, VBas, face recognition, and image quality
- [ ] 7.2 Run real PostgreSQL, Redis, Kafka, four platform services, and operator stubs for PPT-only, ASR-only, teacher-only, student-only, and combined requests
- [ ] 7.3 Verify the five flows complete through Worker-generated state only; fail the Harness if tests call repository completion methods directly
- [ ] 7.4 Verify same-task completed reuse, later task-type append, same-submission T download reuse, later-submission redownload, and exact `effective_params`
- [ ] 7.5 Verify URGENT insertion, unavailable-operator status 30, operator recovery, duplicate Kafka messages, Publisher restart, Worker restart, and offset recovery
- [ ] 7.6 Verify visual refinement, empty behavior, insufficient imagery, stable missing-region fallback, evidence retention, and terminal temporary cleanup
- [ ] 7.7 Verify online image traffic never reaches Kafka or media download, complete requests are not split, and realtime ASR remains sticky and separate from offline ASR
- [ ] 7.8 Capture commands, environment versions, container status, topic offsets, API evidence, metrics, filesystem evidence, and final verdict in Harness scenario records

## 8. 最终架构复审与交接

- [ ] 8.1 Re-run the design-to-implementation evidence matrix and resolve every `不符合` item or explicitly re-scope it through an approved spec update
- [x] 8.2 Create `docs/算法功能调度平台总体设计-v2.md` and its verified PDF, and update platform README, A-service guide, runbook, deployment commands, and diagrams to match the verified runtime rather than intended components; retain the old offline-titled document as a historical baseline
- [ ] 8.3 Run lint, strict type checks, unit, contract, PostgreSQL/Redis integration, real Kafka integration, image build, Compose, and all Harness scenarios
- [ ] 8.4 Record final conformance verdict and remaining non-goals, then decide whether to sync/archive the original and closure changes

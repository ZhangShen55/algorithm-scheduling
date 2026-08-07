## ADDED Requirements

### Requirement: Orchestrator runtime starts real background loops
`orchestrator-service` SHALL start a real Kafka producer, course-command consumer, Outbox publisher, node dispatcher, node executors, task-state aggregator, and terminal cleanup loop during application lifespan, and SHALL close all resources gracefully.

#### Scenario: Service starts with healthy dependencies
- **WHEN** PostgreSQL, Redis, and Kafka are reachable and orchestrator starts
- **THEN** readiness becomes healthy only after the Publisher, Consumer, and executor loops are running

#### Scenario: Kafka consumer loop exits unexpectedly
- **WHEN** a required background loop terminates unexpectedly
- **THEN** readiness becomes unhealthy and the service exits or is restarted instead of continuing as a health-only process

### Requirement: Course commands use a real Kafka broker
The platform SHALL publish Outbox events to a real Kafka topic and SHALL consume them through a committed consumer group. Offsets SHALL be committed only after idempotent pipeline initialization succeeds.

#### Scenario: API commits while Kafka is unavailable
- **WHEN** control-service commits a task and Outbox event while Kafka is unavailable
- **THEN** the event remains pending and is published and consumed after Kafka recovers

### Requirement: Node execution produces state and results
The orchestrator SHALL claim ready nodes by priority and capability, acquire an operator lease, perform required media preparation and adapter calls, persist actual node results, release prerequisites, derive task-type terminal state, and release the lease.

#### Scenario: PPT pipeline completes
- **WHEN** A submits a PPT task and the required contract-compatible operators are ready
- **THEN** Worker-produced results progress from slice through OCR and keywords without a test or operator directly updating repository state

#### Scenario: PPT operator publishes durable shared-path results
- **WHEN** the PPT operator finishes a platform-internal asynchronous slice task
- **THEN** it atomically publishes `/data/result/{task_id}/ppt/manifest.json`, sends one terminal metadata callback without Base64 image bytes, and the orchestrator marks `PPT_SLICE` complete only after validating the manifest and files

#### Scenario: PPT asynchronous capacity remains reserved
- **WHEN** a PPT submission has been accepted but its terminal callback has not committed completion
- **THEN** the orchestrator renews the selected operator lease and releases it only after terminal persistence or a terminal error

#### Scenario: Operator is unavailable
- **WHEN** a ready node has no ready operator capacity
- **THEN** it remains in status 30 while unrelated capabilities continue executing

### Requirement: Visual runtime composes adaptive analysis
`vision-orchestrator-service` SHALL consume course-level visual commands and use a concrete analyzer that performs local frame extraction, caching, adaptive planning, capacity-routed VBas calls, aggregation, evidence publication, progress events, and structured result persistence.

#### Scenario: Teacher writing requires refinement
- **WHEN** coarse scanning finds a writing candidate
- **THEN** the visual runtime performs denser synchronous VBas rounds and persists the refined interval and selected evidence before publishing completion

### Requirement: Runtime-owned cleanup, audit, and metrics
Actual Worker execution SHALL invoke terminal workspace cleanup, node audit logging, task/node/Outbox/Kafka/operator/lease metrics, and task-state aggregation at the corresponding lifecycle boundaries.

#### Scenario: All requested pipelines are terminal
- **WHEN** durable result files exist and all requested nodes are terminal
- **THEN** the Worker removes only `/data/course/{task_id}`, keeps `/data/result/{task_id}`, and records the cleanup outcome

### Requirement: Online HTTP resources close gracefully
`online-gateway-service` SHALL close its shared HTTP client and WebSocket-related resources during application shutdown.

#### Scenario: Gateway stops
- **WHEN** online-gateway receives graceful shutdown
- **THEN** its shared HTTP connection pool is closed without leaking resources

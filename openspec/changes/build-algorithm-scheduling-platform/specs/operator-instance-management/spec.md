## ADDED Requirements

### Requirement: Active operator registration
Every routable operator endpoint SHALL register with `control-service` using an `instance_id`, `operator_code`, capabilities, service URL, model/API versions, GPU/CPU labels, and declared capacity. Operator instances SHALL register with the platform, not with individual adapters.

#### Scenario: Register a VBas instance
- **WHEN** a VBas container starts successfully
- **THEN** it registers `operator_code=vbas`, its supported teacher/student capabilities, endpoint, version, and capacity

### Requirement: Heartbeat and lifecycle state
Registered instances SHALL send periodic heartbeats and SHALL expose `ONLINE`, `DRAINING`, and `OFFLINE` lifecycle states. Expired instances SHALL be excluded from routing.

#### Scenario: Heartbeat expires
- **WHEN** an instance does not heartbeat before its TTL expires
- **THEN** new leases are denied and the instance is shown as offline

#### Scenario: Drain an instance
- **WHEN** operations sets an instance to DRAINING
- **THEN** the instance receives no new work while existing work may complete

### Requirement: Health and status endpoints
Operators SHALL expose `/ops/health`, `/ops/status`, and `/ops/drain` semantics sufficient to distinguish process liveness, model readiness, current capacity, and graceful draining.

#### Scenario: Process is alive but model failed to load
- **WHEN** `/ops/health` confirms the process but `/ops/status` reports the model unavailable
- **THEN** the platform does not route inference to the instance

### Requirement: Atomic capacity lease
The platform SHALL use Redis-backed atomic leases with TTL before routing requests and SHALL release leases after completion or expiry. An operator that has no available capacity SHALL not be selected.

#### Scenario: Concurrent lease attempts for one slot
- **WHEN** two schedulers attempt to reserve the final slot concurrently
- **THEN** exactly one lease succeeds

### Requirement: Deployment endpoint equals registration instance
One independently reachable process/port SHALL be one registered instance. Offline and online ASR SHALL run with one Uvicorn worker per container endpoint and may be deployed on each GPU with distinct ports.

#### Scenario: ASR on two GPUs
- **WHEN** GPU0 and GPU1 each run offline and online ASR on different ports
- **THEN** four independently selectable instances register with the corresponding capability and GPU label

### Requirement: VBas-only platform identity
All new platform contracts SHALL use `vbas`. The platform SHALL NOT expose or persist legacy `tias` aliases, route names, service codes, environment names, or container identities.

#### Scenario: Register with a legacy code
- **WHEN** an instance attempts to register `operator_code=tias`
- **THEN** the platform rejects the registration as an unsupported operator code


## ADDED Requirements

### Requirement: Platform has scoped durable agent instructions
The workspace SHALL contain `algorithm-scheduling-platform/AGENTS.md` defining the platform's durable service boundaries, contracts, dependency ownership, entrypoints, required verification, and prohibited shortcuts. Root `AGENTS.md` SHALL include the platform in the project map without duplicating platform-specific detail.

#### Scenario: An agent modifies orchestrator runtime
- **WHEN** an agent reads the applicable AGENTS files
- **THEN** it is instructed to preserve the four-service boundary and run broker-backed runtime Harness scenarios before claiming completion

### Requirement: Detailed changes live in Harness records
The platform SHALL maintain a `harness/` index, architecture evidence matrix, change ledger, verification commands, and scenario records. Detailed per-change evidence SHALL live in Harness files rather than `AGENTS.md`.

#### Scenario: Runtime wiring changes
- **WHEN** Kafka consumer wiring is implemented or revised
- **THEN** the change ledger records previous state, changed files, contract impact, verification evidence, environment, and remaining risks

### Requirement: Completion claims require evidence tiers
Harness SHALL distinguish static, unit, database integration, broker integration, service runtime, and operator-contract evidence. A requirement SHALL NOT be marked end-to-end complete using only a lower evidence tier.

#### Scenario: Repository test manually completes nodes
- **WHEN** an acceptance test calls repository completion methods instead of a running Worker
- **THEN** Harness classifies it as component/database integration rather than end-to-end

### Requirement: Architecture review is reproducible
The architecture evidence matrix SHALL map each approved design decision to current files, automated commands, current verdict, and known gaps.

#### Scenario: Review after a new release
- **WHEN** the architecture is reviewed again
- **THEN** another engineer can rerun the listed commands and reproduce or challenge every verdict

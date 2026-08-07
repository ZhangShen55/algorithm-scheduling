## ADDED Requirements

### Requirement: Four platform services are deployable together
The repository SHALL provide a validated single-machine deployment definition for `control-service`, `orchestrator-service`, `vision-orchestrator-service`, and `online-gateway-service` with restart policies, readiness checks, shared mounts, network configuration, and dependency settings.

#### Scenario: Start the platform stack
- **WHEN** an operator starts the documented Compose stack with infrastructure available
- **THEN** all four platform services become ready and can reach PostgreSQL, Redis, Kafka, shared storage, and each other through documented addresses

### Requirement: Kafka supports host and container connectivity
Kafka deployment SHALL expose distinct, correct advertised listeners for host-run development and Docker-network service access.

#### Scenario: Platform services run in Docker
- **WHEN** orchestrator connects from the Docker network
- **THEN** it uses the Kafka service-name listener instead of an advertised `127.0.0.1` address

### Requirement: Operator images include the registry client
Every routable operator image SHALL install a versioned `algorithm-scheduling-platform` wheel containing `packages.operator_registry_client`; runtime source mounts and ad hoc `PYTHONPATH` SHALL NOT be required.

#### Scenario: Build an operator image
- **WHEN** the image build completes
- **THEN** an isolated container can import the registry client, start the operator, and expose its business and ops routes

### Requirement: Registration facts are durable
Control-service SHALL persist registration, lifecycle changes, heartbeat summaries, and unregister events in PostgreSQL while Redis remains the authority for current TTL and atomic leases.

#### Scenario: Redis is rebuilt
- **WHEN** Redis state is lost and operators register again
- **THEN** current routing state is reconstructed and prior registration/lifecycle facts remain queryable from PostgreSQL

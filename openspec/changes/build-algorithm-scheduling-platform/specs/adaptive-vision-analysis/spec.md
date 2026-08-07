## ADDED Requirements

### Requirement: Separate visual orchestration from VBas inference
`vision-orchestrator-service` SHALL own video frame planning, iterative analysis, caching, aggregation, and result persistence. VBas SHALL perform frame-level inference only, and the visual service SHALL invoke VBas synchronously through capacity-aware HTTP routing.

#### Scenario: Refinement requires another round
- **WHEN** a coarse teacher frame indicates writing
- **THEN** the visual service creates a denser local frame plan and calls VBas again without asking the course orchestrator to decide individual frame points

### Requirement: Kafka boundary for long visual work
`orchestrator-service` SHALL send course-level visual commands to `vision-orchestrator-service` through Kafka and SHALL receive progress/completion events through Kafka, while iterative frame requests from the visual service to VBas SHALL use synchronous HTTP.

#### Scenario: Teacher behavior command
- **WHEN** a teacher behavior node becomes ready
- **THEN** the orchestrator publishes task and local T-video metadata, and the visual service later publishes progress and completion events

### Requirement: Configurable adaptive scanning
The visual service SHALL support configurable coarse intervals, ordered refinement intervals such as 10/5/2/1 seconds, configurable VBas batch size and concurrency, frame-result caching, and explicit refinement limits.

#### Scenario: Writing candidate at twenty minutes
- **WHEN** coarse scanning detects writing near 20:00
- **THEN** the service expands left and right to bracket state transitions and progressively refines only unresolved boundaries and conflicting points

### Requirement: Behavior interval gap merging
The service SHALL convert detected points into half-open behavior intervals and SHALL merge adjacent intervals when the gap is less than or equal to the behavior's configured `max_gap_seconds`. The initial defaults SHALL merge writing gaps up to 3 seconds and sitting gaps up to 5 seconds.

#### Scenario: Writing from seconds 1-8 and 12-20
- **WHEN** normalization produces `[1,9)` and `[12,21)`
- **THEN** the three-second gap is merged into one writing interval

### Requirement: Empty behavior is a completed business result
When valid analysis completes without a target behavior, the node SHALL remain `status=60`, return an empty interval list, and create no representative evidence image for that behavior. Insufficient valid frames SHALL be distinguished in the Chinese reason from confirmed absence; media or operator failure SHALL use failure state.

#### Scenario: No writing detected with valid coverage
- **WHEN** teacher analysis completes with sufficient valid frames and no writing interval
- **THEN** writing intervals are `[]`, the node is completed, and no writing evidence image is generated

#### Scenario: Teacher is never validly visible
- **WHEN** analysis runs but valid teacher frames are insufficient
- **THEN** the result does not fabricate standing, sitting, writing, or teaching intervals and the reason states that valid imagery was insufficient

### Requirement: Student region metrics and fallback
When front/back polygons are supplied, the visual service SHALL calculate front and back stable-person ratios using detected total persons as denominator. When either polygon is absent, it SHALL generate that region's configured fallback value once per `task_id`, persist it, return it stably, and expose `front_region_provided` and `back_region_provided` booleans.

#### Scenario: Both regions absent
- **WHEN** student behavior runs without `front_points` and `back_point`
- **THEN** the configured front and back fallback values are generated once, reused on every query, and both provided flags are false

### Requirement: Long-term visual evidence
The visual service SHALL retain only selected evidence images under `/data/result/{task_id}/vision`, including existing student head-up, reading, sleeping, phone-use, teacher alert categories and representative frames for writing, sitting, and teaching intervals. Ordinary extracted frames SHALL remain temporary.

#### Scenario: Behavior interval has a representative frame
- **WHEN** a writing interval is confirmed
- **THEN** a selected representative image is stored in the long-term result directory and its file metadata is queryable with the structured interval result


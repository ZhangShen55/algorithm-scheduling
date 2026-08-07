## ADDED Requirements

### Requirement: Reliable asynchronous acceptance
The control service SHALL save course task state and an Outbox event in one PostgreSQL transaction. The orchestrator service SHALL publish pending Outbox events to Kafka and SHALL initialize pipelines idempotently from consumed events.

#### Scenario: API process stops after transaction commit
- **WHEN** a task transaction commits and the API process stops before Kafka publication
- **THEN** the Outbox event remains discoverable and is published after a publisher resumes

### Requirement: Four independent offline pipelines
The orchestrator SHALL expand requested task types into `PPT_SLICE -> PPT_OCR -> PPT_KEYWORDS`, `ASR_TRANSCRIPTION -> COURSE_OVERVIEW`, teacher adaptive visual analysis, and student adaptive visual analysis. It SHALL create only pipelines selected by `task_types`.

#### Scenario: Combined ASR and teacher behavior
- **WHEN** a single request selects ASR and teacher behavior with one teacher video URL
- **THEN** the teacher video is downloaded once for that execution and is shared by both pipelines

#### Scenario: Separate later request
- **WHEN** teacher behavior completed in an earlier request and ASR is requested later
- **THEN** the teacher video is downloaded again and no previously extracted WAV or retained source video is assumed

### Requirement: Dynamic PPT child work
The orchestrator SHALL create OCR and keyword work per generated `ppt_image_id`. Each PPT image SHALL retain identity across slicing, OCR, and keyword extraction, and configurable concurrency SHALL be bounded by available instance capacity.

#### Scenario: Thirty generated slides
- **WHEN** PPT slicing produces 30 valid images
- **THEN** the pipeline tracks 30 OCR items and 30 corresponding keyword items by `ppt_image_id`

### Requirement: Two-level non-preemptive priority
The platform SHALL support `URGENT` and `NORMAL`, default to `NORMAL`, inherit priority from a task-type request to its nodes, and select waiting `URGENT` nodes before waiting `NORMAL` nodes for the same capability. Running work SHALL not be interrupted.

#### Scenario: Urgent work arrives behind normal work
- **WHEN** a NORMAL OCR call is running and an URGENT OCR node becomes ready
- **THEN** the running call completes and the URGENT node receives the next released OCR capacity before waiting NORMAL nodes

### Requirement: Operator-aware waiting
Nodes whose required operator capability has no registered ready instance SHALL remain in `status=30` with a Chinese reason and SHALL not block unrelated pipelines.

#### Scenario: OCR is unavailable but PPT slicing is available
- **WHEN** PPT slicing completes and no OCR instance is ready
- **THEN** `PPT_SLICE` remains completed, `PPT_OCR` waits for an operator, and its slice `path` and `count` remain queryable

### Requirement: Metadata-only Kafka events
Kafka messages SHALL contain identifiers, task type, priority, local paths, and orchestration metadata only. Video, audio, Base64 images, and image binaries SHALL not be carried in Kafka.

#### Scenario: Start a visual pipeline
- **WHEN** the orchestrator publishes a visual analysis command
- **THEN** the event refers to `task_id` and local video path without embedding media bytes


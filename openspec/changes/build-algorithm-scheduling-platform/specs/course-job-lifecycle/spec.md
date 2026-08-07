## ADDED Requirements

### Requirement: Sparse course task submission
The platform SHALL expose `POST /api/course-jobs` and SHALL require a globally unique `task_id` plus one or more values from `PPT`, `ASR`, `TEACHER_BEHAVIOR`, and `STUDENT_BEHAVIOR`. It SHALL validate only fields required by the requested task types and SHALL ignore absent fields belonging exclusively to unrequested task types.

#### Scenario: Submit PPT only
- **WHEN** A submits `task_id`, `task_types=["PPT"]`, and `slides_video_path` without teacher or student fields
- **THEN** the platform accepts the request and creates only the PPT pipeline

#### Scenario: Missing selected task input
- **WHEN** A submits `task_types=["STUDENT_BEHAVIOR"]` without `student_video_path`
- **THEN** the response body reports a Chinese validation reason and no student behavior pipeline is created

### Requirement: Task-type idempotency
The platform SHALL use `(task_id, task_type)` as the idempotency key. A completed pipeline SHALL be returned without duplicate execution, an active pipeline SHALL return its current status, and a previously unrequested task type SHALL be appendable to the same course.

#### Scenario: Query an existing completed task type
- **WHEN** A resubmits an ASR task type that has already completed for the same `task_id`
- **THEN** the platform returns the stored ASR node status and result without creating another ASR execution

#### Scenario: Append a new task type
- **WHEN** PPT has completed and A later submits `TEACHER_BEHAVIOR` for the same `task_id`
- **THEN** the platform creates only the teacher behavior pipeline and preserves the PPT result

### Requirement: Integer task and node statuses
The platform SHALL represent task-type and node state with integer codes: `0` unrequested, `10` pending, `20` waiting for prerequisite, `30` waiting for operator, `40` queued, `50` running, `60` completed, `70` failed, and `80` cancelled. Responses SHALL also include `status_text` and a Chinese `reason`.

#### Scenario: Query an unrequested task type
- **WHEN** A queries a course that has only requested PPT
- **THEN** ASR, teacher behavior, and student behavior are present in the response with `status=0`

### Requirement: Complete course query
The platform SHALL expose `GET /api/course-jobs/{task_id}` and SHALL return all four task types, their internal nodes, current states, available results, file paths, counts, priority, and update times in one response.

#### Scenario: Query while multiple pipelines run
- **WHEN** ASR is complete, PPT OCR is running, and teacher behavior waits for VBas capacity
- **THEN** the query response shows the distinct state and current node for every requested pipeline

### Requirement: Stable ASR options
The ASR task input SHALL accept an optional `asr_options` object. The platform SHALL merge supplied values over defaults `language=auto`, `showSpk=true`, `showEmotion=true`, `showRoleIdentify=false`, `wordTimestamps=false`, and `hotWords=[]`, and SHALL persist the actual `effective_params` used by the completed ASR node.

#### Scenario: Partial ASR option override
- **WHEN** A submits only `asr_options.showRoleIdentify=true`
- **THEN** the ASR adapter sends that value together with all remaining defaults and the node query returns the merged `effective_params`

#### Scenario: Existing ASR result with later parameter changes
- **WHEN** a completed ASR pipeline is submitted again with different options
- **THEN** the platform returns the existing result and the original `effective_params` without rerunning or versioning the ASR result

### Requirement: Northbound response envelope
The normal A-facing HTTP APIs SHALL return HTTP 200 and SHALL express accepted, existing, validation, and business error outcomes through a stable `code`, Chinese `message`, and `data` response body. Internal health, registration, lease, and capacity APIs SHALL retain meaningful HTTP status codes.

#### Scenario: Invalid task-type input
- **WHEN** A omits a path required by the selected task type
- **THEN** the HTTP response is 200 and the response body contains a non-success business code and Chinese message


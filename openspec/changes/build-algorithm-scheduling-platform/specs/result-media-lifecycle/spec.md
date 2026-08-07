## ADDED Requirements

### Requirement: Temporary and durable path separation
The platform SHALL store downloaded videos, extracted WAV files, and ordinary frames under `/data/course/{task_id}` and SHALL store durable PPT slices and selected visual evidence under `/data/result/{task_id}`. Cleanup SHALL delete only the temporary course directory after all requested pipelines reach a terminal state and durable writes complete.

#### Scenario: Delete temporary media after completion
- **WHEN** requested pipelines and durable result writes complete
- **THEN** `/data/course/{task_id}` is removed and `/data/result/{task_id}` remains available

### Requirement: File and structured result distinction
Node responses SHALL use `path` and `count` only for files that actually exist on the shared filesystem. OCR text, keywords, ASR, course overview, behavior intervals, and student statistics SHALL be stored as structured database results and returned under the node's `result` field.

#### Scenario: PPT pipeline completes
- **WHEN** slices, OCR, and keywords all complete
- **THEN** `PPT_SLICE` returns its directory path and count while `PPT_OCR` and `PPT_KEYWORDS` return per-`ppt_image_id` structured results without JSON file paths

### Requirement: Preserve offline ASR response
`ASR_TRANSCRIPTION.result` SHALL preserve the successful v1.1.8 response fields `language`, `segments`, `text`, `speed_info`, `load_audio_time_ms`, and `gpu_time_ms`, including conditional segment fields produced by the effective ASR options. The adapter SHALL treat ASR response bodies containing an error `code` and `msg` as failures even if HTTP status is 200.

#### Scenario: Successful full ASR response
- **WHEN** v1.1.8 returns transcription data
- **THEN** the complete successful response is persisted and returned without replacing it with a platform-defined transcript schema

### Requirement: Preserve course overview response
`COURSE_OVERVIEW.result` SHALL preserve the existing `/v1/course_overviews` success response including `model`, `id`, nested `result.overview`, completion metadata, and token `usage`. Nested result naming SHALL be retained rather than discarded or renamed.

#### Scenario: Course overview succeeds
- **WHEN** text analysis returns a `GenericResponse`
- **THEN** the complete response is persisted under the node's platform-level `result`

### Requirement: Per-slide structured identity
PPT OCR and keyword results SHALL be returned as structured item collections keyed by `ppt_image_id`, and progress SHALL expose `completed_count` and `total_count`.

#### Scenario: Keywords are missing for some slides
- **WHEN** slicing and OCR complete but keyword processing remains incomplete
- **THEN** slice files and completed OCR data remain queryable while the keyword node shows its non-completed state and item progress

### Requirement: Local path semantics
Every `path` returned by the platform SHALL mean an absolute server-local or shared-mount filesystem path, not an HTTP URL. The platform SHALL not imply that A can dereference a path unless A shares or is granted access to that filesystem.

#### Scenario: Return PPT slice location
- **WHEN** A queries a completed slice node
- **THEN** the node returns a path such as `/data/result/course-001/ppt/slices` and does not label it as a URL


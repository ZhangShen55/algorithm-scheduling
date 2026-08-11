# Scenario: PPT Shared Result And Renewable Capacity

## Contract

- Orchestrator submits the canonical `video_path` field with an absolute local shared path under `/data/course/{task_id}`; legacy `uri` is compatibility-only.
- The PPT operator also accepts a remote URL in `video_path`, streams it without persisting the source MP4, and rejects relative local paths.
- Output slides are written under `/data/result/{task_id}/ppt/slices`.
- `manifest.json` is published atomically only after all retained images are closed.
- One terminal callback contains task identity, status, path, manifest path, count, reason and `dynamic_segments`; it contains no Base64 image.
- Orchestrator validates task identity, manifest metadata, dynamic-segment consistency, image count, file existence and symlink-free ancestors before status 60.
- A status 70 callback advances the running node to failed with a Chinese reason; repeated success and failure callbacks are idempotent.
- Orchestrator renews the lease until terminal persistence and still releases capacity if the renewal task has failed.
- `MAX_CONCURRENT_TASKS` and `PLATFORM_DECLARED_CAPACITY` receive the same `PPT_SLICE_CAPACITY` value in Compose.

## Required evidence

Show concurrent tasks up to the configured PPT capacity, duplicate callback idempotency, callback-loss reconciliation, lease renewal beyond the original TTL, OCR release after validation and durable result preservation.

## Current verdict

Operator and platform component verification is partial-pass: shared image/manifest publication, one callback, strict snake_case, `dynamic_segments`, success/failure idempotency, manifest reconciliation, path hardening, renewal HTTP calls and truthful process-level N-way inflight have automated evidence. A synthetic MP4 exercised the HTTP contract and terminal callback, but no real course P video has been accepted as corpus evidence. The orchestrator background executor has not yet connected submission, renewal, terminal persistence and OCR release in one running service, so this scenario is not end-to-end complete.

# Scenario: PPT Shared Result And Renewable Capacity

## Contract

- Orchestrator submits the canonical `video_path` field with an absolute local shared path under `/data/course/{task_id}`; legacy `uri` is compatibility-only.
- The PPT operator also accepts a remote URL in `video_path`, streams it without persisting the source MP4, and rejects relative local paths.
- Output slides are written under `/data/result/{task_id}/ppt/slices`.
- `manifest.json` is published atomically only after all retained images are closed.
- One terminal callback contains task identity, status, path, manifest path and count; it contains no Base64 image.
- Orchestrator validates the manifest before status 60 and renews the lease until terminal persistence.

## Required evidence

Show concurrent tasks up to the configured PPT capacity, duplicate callback idempotency, callback-loss reconciliation, lease renewal beyond the original TTL, OCR release after validation and durable result preservation.

## Current verdict

Operator and platform component verification is partial-pass: shared image/manifest publication, one callback, strict snake_case, duplicate callback handling, manifest reconciliation, renewal HTTP calls and truthful process-level N-way inflight have automated evidence. The orchestrator background executor has not yet connected submission, renewal, terminal persistence and OCR release in one running service, so this scenario is not end-to-end complete.

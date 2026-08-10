# PPT Slice Operator Guide

## Purpose

This service receives a PPT recording video URL, detects slide changes, writes retained images to the platform shared result root and sends one terminal metadata callback.

## Runtime

- Entry point: `app.main:app`
- Conda environment: `ppt_slice`
- Default port: `9001`
- Configuration: `config.toml`

```bash
conda run -n ppt_slice python -m uvicorn app.main:app --host 0.0.0.0 --port 9001 --workers 1
```

## Verification

```bash
conda run -n ppt_slice python -m compileall -q app
curl http://127.0.0.1:9001/health
curl http://127.0.0.1:9001/LocalVideoPPTSliceTasks/v1.0.0/getVersion
```

Use the documented video task and callback fixture for real slicing verification.

## Dynamic Detection Evidence

- OpenSpec owns requirements and design; `harness/` owns iteration records, corpus snapshots, commands, review evidence and reports.
- Never persist remote course MP4 files, complete video copies, MP4 previews or other restorable video artifacts. Probe, decode and sample directly from the URL.
- Static evidence frames, contact sheets and feature caches must stay in Git-ignored Harness artifact directories. Only small JSON, CSV and Markdown summaries may be committed.
- Ad hoc real-course outputs under `test/` are local review artifacts. Keep the directory Git-ignored and never commit its JPEG slices or manifests.
- Re-discover and freeze the complete configured P-video corpus for each acceptance run. Do not hard-code the observed corpus size.
- Do not claim full acceptance until every frozen item is accounted for, every accessible video is processed, candidate and missed-detection reviews are complete, and unresolved or inaccessible items are reported.
- Keep per-run thresholds and evidence in Harness records; do not turn this file into a change journal.

## Algorithm Compatibility Boundary

- When `dynamic_detection.enabled=false`, preserve the legacy path: decode keyframes only, compare adjacent frames and the last saved slide through pixel absolute-difference similarity, and emit no `dynamic_segments`.
- When `dynamic_detection.enabled=true`, use time-sampled reference frames and retain the legacy pixel-similarity decision only for publishing a stable PPT page. Sustained-dynamic detection additionally uses full-frame changed-pixel ratio, active-grid ratio, a four-state temporal detector, bounded segment merging, repeated-dynamic clustering and delayed candidate publication.
- Farneback dense optical flow is continuation evidence only. It may confirm a candidate that strong pixel/grid activity already started and may keep a confirmed segment alive; it must never create a dynamic segment from `STABLE` by itself.
- The public `dynamic_segments[].type` has exactly one supported value: `SUSPECTED_VIDEO_PLAYBACK`. This intentionally covers suspected video playback and continuous scrolling. Harness labels such as `CONFIRMED_VIDEO` and `CONFIRMED_SCROLL` are review metadata and must not leak into the operator API.
- The public segment `reason` may describe direct sustained activity (`sustained_visual_change`) or repeated-segment clustering (`repeated_dynamic_cluster`); `reason` does not create additional segment types.
- Neither the legacy nor current slice-publication path may publish an empty black startup/transition frame. Keep the filter conservative so a dark slide with visible text or other content remains eligible for publication; black-frame filtering must not alter dynamic-state observations.
- Do not claim that motion-only features identify every low-frame-rate or repeated-frame video. The known limitation requires semantic classification or reliable upstream playback metadata before full-corpus acceptance can be declared.

## Result Contract

- Keep `POST /LocalVideoPPTSliceTasks/v1.0.0`.
- Requests and responses use the documented snake_case internal contract.
- Write images only below `{result_root}/{task_id}/ppt/slices` and write `manifest.json` at `{result_root}/{task_id}/ppt/manifest.json`.
- Publish images and the manifest through `.part` files followed by atomic replacement.
- Send exactly one terminal callback containing metadata only. Never include Base64 image data.
- `task_id` and `operator_task_id` must not be usable for path traversal.
- A single Uvicorn worker enforces `max_concurrent_tasks` atomically.

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

## Result Contract

- Keep `POST /LocalVideoPPTSliceTasks/v1.0.0`.
- Requests and responses use the documented snake_case internal contract.
- Write images only below `{result_root}/{task_id}/ppt/slices` and write `manifest.json` at `{result_root}/{task_id}/ppt/manifest.json`.
- Publish images and the manifest through `.part` files followed by atomic replacement.
- Send exactly one terminal callback containing metadata only. Never include Base64 image data.
- `task_id` and `operator_task_id` must not be usable for path traversal.
- A single Uvicorn worker enforces `max_concurrent_tasks` atomically.

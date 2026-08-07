# VBas Operator Guide

## Scope

`vbas` is the independently deployable teacher/student visual behavior inference operator. It accepts image batches and returns per-image detections. It does not download course videos, extract frames, consume Kafka course tasks, aggregate timelines or write course-level result tables.

Course visual orchestration is maintained separately:

- Local: `/Users/zhangshen/Documents/workspace/jy-vision-orchestrator-server`
- Git: `git@github.com:ZhangShen55/jy-vision-orchestrator-server.git`

Do not reintroduce the removed `ai_quality` implementation into this project.

## Layout And Entrypoint

- Python package: `app/`
- FastAPI entrypoint: `app.main:app`
- Configuration: root `config.toml`, overridable with `CONFIG_PATH`
- Plain models: root `models/`
- Encrypted models: root `models-encrypted/`
- DirectMHP source: `app/vendor/DirectMHP/`
- Docker assets: `docker/`

Start from the `vbas` project root:

```bash
conda run -n jy-tias python -m uvicorn app.main:app --host 127.0.0.1 --port 8981
```

## Compatibility Constraints

- Preserve `/ImageDetect/student/v1.0.0` and `/ImageDetect/teacher/v1.0.0` request and response schemas.
- Preserve `/AE/Health`, `/AE/WorkerStatus` and `/AE/Drain`.
- Do not restore removed legacy sync-task, capacity, version or log-level routes without explicit approval.
- The `[TIAS]` section, `AiQualityBaseUrl`, `TIAS_*` environment variables and `tias_model_key` names are compatibility identifiers. Do not rename them as part of layout work.
- `StoragePath` continues to accept the formats already supported by the implementation: Base64/Data URL, HTTP(S) URL, absolute path or a path relative to `IMAGE_ROOT`.

## Local Environment

- Conda environment: `jy-tias`
- Local verification port: `8981`
- CPU mode: set root `config.toml` `GPU_ID = "cpu"`
- NVIDIA mode: set `GPU_ID` to the configured GPU identifier used by the existing deployment.

## Verification

From the project root:

```bash
conda run -n jy-tias python -m compileall -q app scripts tests
conda run -n jy-tias python -m pytest -q tests
conda run -n jy-tias python -m pip check
```

Then start `app.main:app`, verify `/AE/Health`, and directly call both student and teacher inference endpoints with one image from `tests/images/`. Do not depend on the external visual orchestrator for the direct inference smoke test.

## Documentation

Update this file only when durable boundaries, entrypoints, paths, dependencies or required verification change. Normal code changes belong in Git history rather than a per-change `AGENTS.md` journal.

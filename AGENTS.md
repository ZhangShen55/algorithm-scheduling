# Algorithm Operators Workspace Guide

## Scope

This workspace contains independently deployable algorithm operators and the algorithm scheduling platform that calls them. Keep operator HTTP/WebSocket contracts stable unless the user explicitly approves a contract change. The approved PPT internal shared-path contract is the only current exception.

## Project Map

| Project | Purpose | Local Conda env | Local verification port |
| --- | --- | --- | --- |
| `asr_online` | Realtime streaming ASR | `asr` | `8084` |
| `asr_offline` | Offline course audio ASR | `asr` | `8083` |
| `facerec` | Face detection, embedding and recognition | `facerecapi` | `8003` |
| `ocr` | PPT image OCR | `ocr-v6` | `8866` |
| `screen_det` | Single-image quality detection through `detect_all` | `screen_det` | `8880` |
| `ppt_slice` | PPT video slide slicing | `ppt_slice` | `9001` |
| `vbas` | Teacher/student visual behavior inference | `jy-tias` | `8981` |
| `text_analysis` | Course mind-map and PPT keyword text analysis | `ai_report` | `8000` |
| `algorithm-scheduling-platform` | Four-service control, offline orchestration, visual orchestration and online routing platform | project `.venv` | `18100-18103` |

The old VBas orchestration and aggregation implementation has moved to a separate project:

- Local path: `/Users/zhangshen/Documents/workspace/jy-vision-orchestrator-server`
- Git remote: `git@github.com:ZhangShen55/jy-vision-orchestrator-server.git`

Do not reintroduce that orchestration, aggregation or database-writing logic into `vbas`.

The platform selects registered VBas instances and owns all course-level visual orchestration. VBas remains frame inference only. Platform-specific service boundaries and evidence requirements are defined in `algorithm-scheduling-platform/AGENTS.md`.

## Required Layout

Every operator exposes a real Python package named `app` and an application object at `app.main:app`. This operator layout rule does not replace the platform-specific layout in its own `AGENTS.md`. Use this startup shape for local and container execution:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port PORT --workers WORKERS
```

Use absolute imports beginning with `app.` for cross-package imports. Do not rely on symlinks, ad hoc `PYTHONPATH` values or a specific current working directory.

Keep `config.toml`, model directories, Docker files, scripts, tests and README files at the project root unless a project-specific `AGENTS.md` says otherwise. Resolve configuration and model paths from an explicit project root. `CONFIG_PATH` may override the default root `config.toml`.

## Compatibility Boundaries

- Do not change existing HTTP/WebSocket paths, methods, request fields, response fields or default service ports as part of structural work.
- Online image operators accept images supplied by the upstream service. Do not add RTSP ingestion or frame extraction to `facerec`, `screen_det` or online VBas endpoints.
- Keep existing `model/` versus `models/` directory names; path-name unification is not part of the current migration.
- Preserve user changes in dirty worktrees. Never reset, clean or discard files you did not create.

## Verification

For each changed operator:

1. Run `python -m compileall -q app` in its documented Conda environment.
2. Verify `from app.main import app`.
3. Run the project tests relevant to the change.
4. Start with `python -m uvicorn app.main:app` and check health/readiness.
5. Run one real inference using the project fixture described in its `AGENTS.md`.
6. Confirm route paths and methods remain compatible.

VBas verification must call its student and teacher inference APIs directly. Do not use the extracted visual orchestrator as a VBas health dependency.

## Documentation Rules

Update `README.md`, Docker/Compose files, shell scripts and path-related Markdown whenever a directory, entrypoint, configuration path or deployment command changes.

`AGENTS.md` stores durable operating instructions, not a per-change journal. Update it only when project boundaries, layout, entrypoints, dependencies, configuration locations, required tests or compatibility constraints change. Record normal changes in Git history; record release behavior changes in `CHANGELOG.md` or the relevant design document.

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
| `control_service` | Course task control API, task facts, Outbox and operator registry | platform `.venv` | `18100` |
| `orchestrator_service` | Outbox publication, offline DAG and general node execution | platform `.venv` | `18101` |
| `vision_orchestrator_service` | Offline teacher/student adaptive visual orchestration | platform `.venv` | `8010` (`18102` through Compose) |
| `online_gateway_service` | Online image routing and realtime ASR WebSocket gateway | platform `.venv` | `8001` (`18103` through Compose) |
| `algorithm-scheduling-platform` | Shared packages, migrations, deployment definitions, cross-service tests and Harness | project `.venv` | N/A |

The old VBas orchestration and aggregation implementation has moved to a separate project:

- Local path: `/Users/zhangshen/Documents/workspace/jy-vision-orchestrator-server`
- Git remote: `git@github.com:ZhangShen55/jy-vision-orchestrator-server.git`

Do not reintroduce that orchestration, aggregation or database-writing logic into `vbas`.

The platform selects registered VBas instances and owns all course-level visual orchestration. VBas remains frame inference only. The four deployable platform services live at the workspace root. Shared packages, migrations, deployment definitions, cross-service tests and Harness remain under `algorithm-scheduling-platform/`; its `AGENTS.md` defines their service boundaries and evidence requirements.

## Required Layout

Every operator and root-level platform service exposes a real Python package named `app` and an application object at `app.main:app`. Use this startup shape for local and container execution:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port PORT --workers WORKERS
```

Algorithm operators use absolute imports beginning with `app.` for cross-package imports. Root-level platform services use package-relative imports inside `app` so cross-service tests can import them as `control_service.app`, `orchestrator_service.app`, `vision_orchestrator_service.app` and `online_gateway_service.app` without colliding top-level `app` modules. Neither project type may rely on symlinks, ad hoc `PYTHONPATH` values or an undocumented current working directory.

Each root-level platform service independently owns `app/`, `tests/`, `docker/Dockerfile`, `config.toml`, `requirements.txt` and `README.md`. Its canonical deployment entrypoint is only `app.main:app`; do not restore `services.<service_name>` compatibility imports or runtime code beneath `algorithm-scheduling-platform/services`.

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

# Scenario: Root Service Relocation

## Contract

- `control_service`, `orchestrator_service`, `vision_orchestrator_service` and `online_gateway_service` are workspace-root FastAPI projects.
- Each service starts from its own directory with `app.main:app` and does not import `services.<service_name>`.
- The shared platform distribution packages `packages*` only.
- Each Docker image contains the current service and installed shared distribution, not sibling service source; the root build context uses an explicit allowlist.
- HTTP/WebSocket contracts, default container ports, Compose host mappings and operator registration semantics remain unchanged.

## Required Evidence

Run root layout and contract tests, compile/import each service independently, parse all Compose definitions, build all four images, start each image, check `/health`, and inspect `/app` for sibling service source. Scan effective source and deployment files for old runtime paths.

## Current Verdict

Pass on 2026-08-07. Four service suites and the 192-test platform suite passed; Ruff and strict Mypy passed; all Compose definitions parsed; the runtime/build/documentation old-path gate passed; all four images built through the root allowlist context and returned HTTP 200 from `/health`; image inspection found only the current service under `/app/app`. Broker-backed course processing remains covered by the separate runtime-closure scenario.

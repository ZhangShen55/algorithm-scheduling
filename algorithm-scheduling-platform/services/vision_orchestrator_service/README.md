# Vision Orchestrator Service

The service package owns vision orchestration policies, command processing adapters,
VBas access, evidence selection, and typed configuration. Existing flat imports under
`services.vision_orchestrator_service` remain compatibility shims.

The canonical entrypoint is:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --workers 1
```

Configuration loads built-in typed defaults, then `config.toml`, then `VISION_`
environment variables with `__` between nested names. Set `CONFIG_PATH` to load a
different TOML file.

`/health` and `/ready` currently report process health/readiness. The existing domain
and adapter code is preserved; this scaffold does not claim a complete Kafka consumer
or long-running worker lifecycle.

The Dockerfile is a syntactically valid service placeholder. A production image still
needs the monorepo `packages` dependencies included in its build context or installed
as packages.

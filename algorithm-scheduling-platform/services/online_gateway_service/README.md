# Online Gateway Service

The service exposes the existing online VBas, face recognition, image-quality, and
realtime ASR proxy contracts. Existing flat imports under
`services.online_gateway_service` remain compatibility shims.

The canonical entrypoint is:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 1
```

Configuration loads built-in typed defaults, then `config.toml`, then `ONLINE_`
environment variables with `__` between nested names. Set `CONFIG_PATH` to load a
different TOML file.

`/ready` currently reports process readiness without probing the control service. The
existing request-level proxy and WebSocket relay maturity is unchanged; this scaffold
does not claim additional runtime supervision.

The Dockerfile is a syntactically valid service placeholder. A production image still
needs the monorepo `packages` dependencies included in its build context or installed
as packages.

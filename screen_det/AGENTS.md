# Screen Detection Operator Guide

## Purpose

This service performs image quality detection. The scheduling platform uses the `detect_all` capability with images supplied directly by the upstream service. Do not add RTSP ingestion, stream ownership or frame extraction here.

## Runtime

- Entry point: `app.main:app`
- Conda environment: `screen_det`
- Default port: `8880`
- Configuration: `config.toml`
- Models: `model/`

```bash
conda run -n screen_det python -m uvicorn app.main:app --host 0.0.0.0 --port 8880 --workers 1
```

## Verification

```bash
conda run -n screen_det python -m compileall -q app
conda run -n screen_det python -m pytest -q tests
curl http://127.0.0.1:8880/health
```

Run the existing single-image `detect_all` fixture after startup. Keep request and response fields stable.

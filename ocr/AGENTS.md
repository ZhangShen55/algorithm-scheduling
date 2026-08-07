# OCR Operator Guide

## Purpose

This service recognizes text and optional formula content from one PPT image per request. Keep existing OCR routes and response models compatible.

## Runtime

- Entry point: `app.main:app`
- Conda environment: `ocr-v6`
- Default port: `8866`
- Configuration: `config.toml`
- Models: `models/`

Start from the project root:

```bash
conda run -n ocr-v6 python -m uvicorn app.main:app --host 0.0.0.0 --port 8866 --workers 1
```

## Verification

```bash
conda run -n ocr-v6 python -m compileall -q app
conda run -n ocr-v6 python -m pytest -q tests
curl http://127.0.0.1:8866/ocr/getVersion
conda run -n ocr-v6 python scripts/smoke_test.py
```

Update README and Docker files whenever the entrypoint, configuration path, model path or deployment command changes.

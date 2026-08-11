# Face Recognition Operator Guide

## Purpose

This service detects faces, extracts ArcFace embeddings and recognizes people against MongoDB records. Online requests contain an upstream-provided image; do not add video-stream ingestion or frame extraction.

## Runtime

- Entry point: `app.main:app`
- Conda environment: `facerecapi`
- Local verification port: `8003`
- Container port: `8000`
- Configuration: root `config.toml`, overridable with `CONFIG_PATH`
- Models: root `ai_models/`
- Mutable media: root `media/`
- Database: MongoDB configured by `[db]`
- Local Python: 3.10; `fastdeploy-python==1.0.7` ships a macOS CPython 3.10 extension and currently blocks a 3.11 upgrade

`[gpu].device` accepts only `cpu` or `cuda:N` such as `cuda:0`.

```bash
conda run -n facerecapi python -m uvicorn app.main:app --host 127.0.0.1 --port 8003 --workers 1
```

## Input Contract

`/recognize` accepts one image in a Base64 Data URL such as `data:image/png;base64,...`. Preserve existing request and response fields.

`[image].save_person_photo` defaults to `false`. When disabled, `/persons` and batch person imports must still generate and persist non-empty embeddings and remain recognizable, while returning an empty `photo_path` and writing no person image. Do not replace FastDeploy with another inference backend without embedding and threshold equivalence evidence.

## Verification

```bash
conda run -n facerecapi python -m compileall -q app
conda run -n facerecapi python -m pytest -q tests
conda run -n facerecapi python -m pip check
curl http://127.0.0.1:8003/ops/health
```

Use `tests/data/常泽宇.png` for real recognition. A temporary `app` symlink is forbidden; imports must work from the real package.

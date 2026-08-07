# Realtime ASR Operator Guide

## Purpose

This service performs realtime Chinese speech transcription for live-player subtitles. It is a WebSocket inference operator and does not own course-result persistence.

## Runtime

- Entry point: `app.main:app`
- Conda environment: `asr`
- Local verification port: `8084`
- WebSocket: `/v1.0.1/seacraft_asr_online`
- Configuration: root `config.toml`, overridable with `CONFIG_PATH`
- Models: root `model/`, overridable with `MODEL_BASE_DIR`

```bash
conda run -n asr python -m uvicorn app.main:app --host 127.0.0.1 --port 8084 --workers 1
```

## Audio Contract

Send 16 kHz, mono, signed 16-bit PCM chunks over WebSocket. Keep response keys, timing semantics and route path stable.

## Verification

```bash
conda run -n asr python -m compileall -q app
conda run -n asr python -m unittest discover -s tests -v
conda run -n asr python -m pip check
```

Use `test/chinEng-16k.wav`; send 7680 samples per chunk and verify non-empty incremental text. Update both regular and Cython Docker layouts when the package structure changes.

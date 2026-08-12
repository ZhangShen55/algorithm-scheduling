# Offline ASR Operator Guide

## Purpose

This service transcribes completed course audio and returns offline ASR results for persistence by the scheduling workflow. Preserve the `/v1.1.8/seacraft_asr` contract. The retired `/v1.1.7/seacraft_asr` and `/audio/detect_mandarin` routes must not be restored.

## Runtime

- Entry point: `app.main:app`
- Conda environment: `asr`
- Local verification port: `8083`
- Configuration: root `config.toml`, overridable with `CONFIG_PATH`
- Models: root `model/`
- Audio fixtures: root `test_wav/`

```bash
conda run -n asr python -m uvicorn app.main:app --host 127.0.0.1 --port 8083 --workers 1
```

## Verification

```bash
conda run -n asr python -m compileall -q app
conda run -n asr python -m unittest discover -s tests -v
conda run -n asr python -m pip check
```

Use `test_wav/chinEng-16k.wav` or a short derived WAV for real CPU transcription. Verify non-empty text and segments; the legacy response field `gpu_time_ms` remains unchanged even in CPU mode for API compatibility.

Route verification must confirm that `/v1.1.8/seacraft_asr`, `/audio/db_snr`, and `/text/question` remain available, while `/v1.1.7/seacraft_asr` and `/audio/detect_mandarin` return 404 and do not appear in OpenAPI.

When the local fixture `/Volumes/Data55/asr测试文件/法语音频.mp3` is available and `open_mul_lang=true`, use it for an optional real French inference through v1.1.8. Verify `language=fr`, non-empty transcription, monotonic word timestamps within the audio duration, at least one non-empty `segment_words`, at least one positive segment `speed`, and 1/5/10-minute `speed_info`. Do not copy this fixture into the repository.

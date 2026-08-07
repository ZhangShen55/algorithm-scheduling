# Offline ASR Operator Guide

## Purpose

This service transcribes completed course audio and returns offline ASR results for persistence by the scheduling workflow. Preserve the existing `/v1.1.7/seacraft_asr` and `/v1.1.8/seacraft_asr` contracts.

## Runtime

- Entry point: `app.main:app`
- Conda environment: `asr`
- Local verification port: `8083`
- Configuration: root `config.toml`, overridable with `CONFIG_PATH`
- Models: root `model/`
- Audio fixtures: root `test_wav/`

```bash
TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1 \
conda run -n asr python -m uvicorn app.main:app --host 127.0.0.1 --port 8083 --workers 1
```

PyTorch 2.6 defaults `torch.load` to `weights_only=True`; the current trusted local Pyannote checkpoint requires the compatibility environment variable above. Never use it to load untrusted checkpoints.

## Verification

```bash
conda run -n asr python -m compileall -q app
conda run -n asr python -m unittest discover -s tests -v
conda run -n asr python -m pip check
```

Use `test_wav/chinEng-16k.wav` or a short derived WAV for real CPU transcription. Verify non-empty text and segments; the legacy response field `gpu_time_ms` remains unchanged even in CPU mode for API compatibility.

# ASR Offline v1.1.8 Multilingual Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/v1.1.8/seacraft_asr` the sole offline ASR endpoint, route `fr` to Whisper without speaker/emotion enhancement, preserve the existing response shape and speech-rate factor, and remove the Mandarin/v1.1.7/Pyannote runtime surface.

**Architecture:** Validate and normalize `language` at the v1.1.8 route boundary before audio processing, then dispatch `auto/zh/en` to the existing Paraformer pipeline and `fr` to a focused Whisper-only pipeline. Consume Faster-Whisper's lazy segments while the model lock and GPU slot are held. Keep existing response keys, make Unicode-aware speech-rate counting reusable by both pipelines, and remove only Pyannote-specific runtime/config/deployment pieces.

**Tech Stack:** Python 3.11, FastAPI, Faster-Whisper 1.1.1, FunASR, unittest/pytest, TOML, Docker Compose.

---

## File Structure

- Create `asr_offline/tests/test_v118_multilingual.py`: route dispatch, response compatibility, error, timestamp, speed, and removal contract tests.
- Modify `asr_offline/app/api/routes/asr_v18.py`: normalize/validate language and dispatch Paraformer versus Whisper.
- Delete `asr_offline/app/api/routes/asr_v17.py`: remove the retired endpoint and its Pyannote branch.
- Modify `asr_offline/app/api/routes/audio.py`: retain DB/SNR analysis and remove Mandarin detection only.
- Modify `asr_offline/app/api/routes/asr_common.py`: add the focused Whisper-only result builder or shared context support without changing Paraformer behavior.
- Modify `asr_offline/app/core/concurrency.py`: consume the Whisper generator inside the thread/model lock/timeout.
- Modify `asr_offline/app/utils/feature_utils.py`: make content counting Unicode-aware while preserving Chinese counting and `rate_factor` behavior.
- Modify `asr_offline/app/main.py`: assemble only supported route modules.
- Modify `asr_offline/app/core/models.py`, `asr_offline/app/core/config.py`: remove Pyannote-specific state/config and keep FiveWh lazy loading.
- Delete `asr_offline/app/utils/pynanote_speaker.py`: remove unused Pyannote merger.
- Modify `asr_offline/config.toml`, `algorithm-scheduling-platform/deploy/config/operators/asr_offline.gpu.toml`: remove Pyannote keys in lockstep.
- Modify `asr_offline/requirements.txt`, `asr_offline/requirements-pip.txt`, `asr_offline/docker/Dockerfile`, `asr_offline/.dockerignore`: remove Pyannote dependency/setup and exclude its local model assets from new images.
- Modify `algorithm-scheduling-platform/deploy/docker-compose.operators.yml` and related deployment tests: remove the Pyannote-only torch compatibility variable and retired route assertion.
- Modify `asr_offline/README.md`, `asr_offline/AGENTS.md`, platform ASR contract documents: document the approved route and response behavior.

### Task 1: Lock the v1.1.8 routing and response contract with failing tests

**Files:**
- Create: `asr_offline/tests/test_v118_multilingual.py`
- Modify: `asr_offline/tests/test_optimizations.py`

- [ ] **Step 1: Add route, dispatch, response, speed, and generator-lifecycle tests**

Create tests with lightweight fake contexts and Faster-Whisper objects that assert:

```python
SUPPORTED_PARA_LANGUAGES = ("auto", "zh", "en")

def test_retired_routes_are_absent_from_openapi():
    paths = create_app().openapi()["paths"]
    assert "/v1.1.7/seacraft_asr" not in paths
    assert "/audio/detect_mandarin" not in paths
    assert "/v1.1.8/seacraft_asr" in paths
    assert "/audio/db_snr" in paths
    assert "/text/question" in paths

def test_french_unicode_words_keep_accents_and_internal_apostrophes():
    assert count_content_words("L’école française aujourd’hui") == 3

async def test_whisper_generator_is_consumed_inside_model_lock():
    # The fake generator records concurrency._model_lock.locked() on each yield.
    segments, _ = await transcribe_with_gpu_lock(FakeWhisper())
    assert list(segments) == ["bonjour"]
    assert FakeWhisper.lock_states == [True]
```

Use direct calls to `api_asr_v18()` with patched `prepare_asr_context`,
`run_paraformer_asr`, `get_whisper_model`, and a fake Whisper model to assert:

- normalization of `" Auto "`, `"ZH"`, `"EN"`, and `" FR "`;
- unknown/empty languages return business `4009` before context preparation;
- disabled/unready French returns business `4003` before context preparation;
- French ignores speaker/role/emotion model paths and emits only approved existing fields;
- requested `role`/`emotion` are `None`, unrequested fields are absent;
- `wordTimestamps=false` emits `[]`, while `true` maps real fake words;
- every French segment has `speed`, and `speed_info` keeps 1/5/10-minute arrays;
- empty Whisper results return `4008`.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
conda run -n asr python -m unittest \
  tests.test_v118_multilingual \
  tests.test_optimizations.GpuConcurrencyTests -v
```

Expected: failures because v1.1.7 and Mandarin routes still exist, v1.1.8 does not route French, Unicode counting is ASCII-only, and the generator is consumed after the lock is released.

- [ ] **Step 3: Commit the failing tests**

```bash
git add asr_offline/tests/test_v118_multilingual.py asr_offline/tests/test_optimizations.py
git commit -m "test(asr): 锁定v1.1.8小语种合同"
```

### Task 2: Implement the v1.1.8 Whisper-only French path

**Files:**
- Modify: `asr_offline/app/api/routes/asr_v18.py`
- Modify: `asr_offline/app/api/routes/asr_common.py`
- Modify: `asr_offline/app/core/concurrency.py`
- Modify: `asr_offline/app/utils/feature_utils.py`
- Modify: `asr_offline/app/main.py`
- Modify: `asr_offline/app/api/routes/audio.py`
- Delete: `asr_offline/app/api/routes/asr_v17.py`

- [ ] **Step 1: Consume Whisper segments in the locked worker**

Change `transcribe_with_gpu_lock()` so the synchronous function submitted to the thread performs both `model.transcribe()` and `list(segments)`:

```python
def _transcribe_and_collect():
    segments, info = model.transcribe(*args, **kwargs)
    return list(segments), info

async with _model_lock:
    return await asyncio.wait_for(
        asyncio.to_thread(_transcribe_and_collect),
        timeout=60 * 60,
    )
```

- [ ] **Step 2: Implement Unicode-aware content counting**

Normalize with `unicodedata.normalize("NFC", text)`, keep the existing Chinese-per-character behavior, and scan remaining characters using Unicode categories. Count sequences of letters/marks/numbers as one word; allow `'`, `’`, and `-` only between word characters. Preserve existing numeric behavior and do not change the `calculate_speech_rate()` formula or fallback policy beyond what tests require.

- [ ] **Step 3: Add a focused Whisper result builder**

Build each raw Whisper segment from `seg.text`, `seg.start`, `seg.end`, and optional
`seg.words`. Use raw float timestamps for `calculate_speech_rate(...,
settings.speech_rate_factor)`, format response timestamps to two decimals, and add:

```python
if request.showSpk or request.showRoleIdentify:
    item["role"] = None
if request.showEmotion:
    item["emotion"] = None
```

Always return `speed` and `segment_words`; only expose words when requested. Call
`build_speed_info(output_segments, total_duration=ctx.audio_total_s)`. Return `4008`
when no valid segment was produced. Do not add top-level or per-segment status fields.

- [ ] **Step 4: Dispatch at the v1.1.8 boundary before audio preparation**

Normalize `request.language`. For `auto/zh/en`, prepare context and call the unchanged
Paraformer pipeline. For `fr`, first verify `settings.open_mul_lang` and
`get_whisper_model()`, then prepare context and call the Whisper builder. For all other
values, return `4009` without preparing audio. Keep cleanup in `finally` only after a
context exists.

- [ ] **Step 5: Remove the retired routes**

Remove the v1.1.7 router import/include and delete its module. Delete only
`audio_mandarin_detect()` plus now-unused imports from `audio.py`; retain
`audio_analyze()`. Keep the FiveWh router unchanged.

- [ ] **Step 6: Run the focused tests and verify GREEN**

Run:

```bash
conda run -n asr python -m unittest \
  tests.test_v118_multilingual \
  tests.test_optimizations.GpuConcurrencyTests -v
```

Expected: all focused tests pass.

- [ ] **Step 7: Run the complete operator unit suite**

```bash
conda run -n asr python -m unittest discover -s tests -v
```

Expected: all tests pass; update only assertions whose old behavior was explicitly retired.

- [ ] **Step 8: Commit the implementation**

```bash
git add asr_offline/app asr_offline/tests
git commit -m "feat(asr): 将小语种转写收敛到v1.1.8"
```

### Task 3: Remove Pyannote runtime and deployment resources

**Files:**
- Modify: `asr_offline/tests/test_gpu_runtime.py`
- Modify: `asr_offline/app/core/models.py`
- Modify: `asr_offline/app/core/config.py`
- Delete: `asr_offline/app/utils/pynanote_speaker.py`
- Modify: `asr_offline/config.toml`
- Modify: `algorithm-scheduling-platform/deploy/config/operators/asr_offline.gpu.toml`
- Modify: `asr_offline/requirements.txt`
- Modify: `asr_offline/requirements-pip.txt`
- Modify: `asr_offline/docker/Dockerfile`
- Modify: `asr_offline/.dockerignore`
- Modify: `algorithm-scheduling-platform/deploy/docker-compose.operators.yml`
- Modify: `algorithm-scheduling-platform/tests/test_milestone_2b_gpu_fail_fast.py`
- Modify: `algorithm-scheduling-platform/tests/test_operator_deployment_integration.py`

- [ ] **Step 1: Write failing absence/config-shape tests**

Update GPU runtime tests so enabled model construction covers Paraformer, emotion,
Whisper and lazy BERT only, without a `PyannotePipeline` symbol. Add assertions that:

```python
for path in (Path("requirements.txt"), Path("requirements-pip.txt")):
    assert "pyannote.audio" not in path.read_text()
assert "open_mul_spk" not in Path("config.toml").read_text()
assert "pyannote_model_yml" not in Path("config.toml").read_text()
```

Update platform deployment tests to assert the retired v1.1.7 source contract and
`TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD` environment entries are absent.

- [ ] **Step 2: Run the resource tests and verify RED**

Run:

```bash
conda run -n asr python -m unittest \
  tests.test_gpu_runtime \
  tests.test_optimizations.RequirementsPinningTests -v
../../algorithm-scheduling-platform/.venv/bin/python -m pytest \
  algorithm-scheduling-platform/tests/test_milestone_2b_operator_configs.py \
  algorithm-scheduling-platform/tests/test_milestone_2b_gpu_fail_fast.py \
  algorithm-scheduling-platform/tests/test_operator_deployment_integration.py -q
```

Expected: absence assertions fail against the current Pyannote files and deployment contract.

- [ ] **Step 3: Remove Pyannote code and configuration**

Delete Pyannote imports, `_model_speaker`, its load block/getter, the two settings
properties, and `pynanote_speaker.py`. Remove `pyannote_model_yml` and `open_mul_spk`
from both TOML files without changing `open_mul_lang`, `open_spk`, `open_emotion`, or
`open_fivewh`.

- [ ] **Step 4: Remove dependency and container compatibility setup**

Delete `pyannote.audio==3.3.2` from both requirements files, remove Dockerfile's
speaker-diarization `sed` block, and add the three Pyannote-only model directories to
`.dockerignore`. Remove `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD` from all three ASR Compose
instances and update its test while retaining the `REQUIRE_GPU=true` assertions.

- [ ] **Step 5: Update the static route contract**

Change `test_operator_business_routes_and_default_ports_remain_compatible()` so
`asr_offline` lists only `app/api/routes/asr_v18.py` and
`/v1.1.8/seacraft_asr`.

- [ ] **Step 6: Run resource and deployment tests and verify GREEN**

Run the same commands from Step 2. Expected: all pass.

- [ ] **Step 7: Commit resource cleanup**

```bash
git add asr_offline algorithm-scheduling-platform/deploy \
  algorithm-scheduling-platform/tests/test_milestone_2b_gpu_fail_fast.py \
  algorithm-scheduling-platform/tests/test_operator_deployment_integration.py
git commit -m "refactor(asr): 移除小语种Pyannote资源"
```

### Task 4: Synchronize operator and platform documentation

**Files:**
- Modify: `asr_offline/README.md`
- Modify: `asr_offline/AGENTS.md`
- Modify: `docs/离线课程任务调度处理设计.md`
- Modify: `docs/算法功能调度平台总体设计-v2.md`
- Modify: `algorithm-scheduling-platform/deploy/A服务接口与部署对接指南.md`

- [ ] **Step 1: Update operator documentation**

Document v1.1.8 as the sole route, its language routing matrix, `open_mul_lang` business
error, French `role/emotion` null behavior, real/empty `segment_words`, and the existing
`speed/speed_info` shape. Set all documented speech-rate examples/defaults to the
approved `rate_factor=0.4`. Remove v1.1.7, Mandarin, `open_mul_spk`, Pyannote model,
dependency, helper and startup compatibility references. Keep `/text/question`.

- [ ] **Step 2: Update durable operator instructions**

Change `AGENTS.md` to require only v1.1.8 compatibility and add the optional local French
fixture for real validation. Remove the Pyannote-specific trusted-checkpoint environment
instruction. Do not write a per-change journal.

- [ ] **Step 3: Update platform contract documents**

In all three platform documents, retain the existing successful response shape and clarify
that for `fr`, requested role/emotion fields are present with JSON null while
`segment_words` follows its flag. Do not add `feature_status` or any new field. Remove
claims that every `showSpk/showEmotion=true` request yields actual analysis for every
language.

- [ ] **Step 4: Check docs and commit**

Run:

```bash
rg -n '/v1\.1\.7/seacraft_asr|audio/detect_mandarin|open_mul_spk|pyannote_model_yml' \
  asr_offline algorithm-scheduling-platform/deploy docs \
  --glob '!docs/superpowers/plans/2026-08-03-*'
git diff --check
```

Expected: no active documentation/config/code reference to retired surfaces; historical
plans may remain untouched.

```bash
git add asr_offline/README.md asr_offline/AGENTS.md \
  docs/离线课程任务调度处理设计.md \
  docs/算法功能调度平台总体设计-v2.md \
  algorithm-scheduling-platform/deploy/A服务接口与部署对接指南.md
git commit -m "docs(asr): 更新v1.1.8多语言合同"
```

### Task 5: Verify the full operator contract and real French inference

**Files:**
- Modify only if verification reveals a regression, always by adding a failing test first.

- [ ] **Step 1: Run static and unit verification**

```bash
cd asr_offline
conda run -n asr python -m compileall -q app
conda run -n asr python -c 'from app.main import app; print(sorted(app.openapi()["paths"]))'
conda run -n asr python -m unittest discover -s tests -v
cd ..
algorithm-scheduling-platform/.venv/bin/python -m pytest \
  algorithm-scheduling-platform/tests/test_offline_asr_adapter.py \
  algorithm-scheduling-platform/tests/test_milestone_2b_operator_configs.py \
  algorithm-scheduling-platform/tests/test_milestone_2b_gpu_fail_fast.py \
  algorithm-scheduling-platform/tests/test_operator_deployment_integration.py -q
```

Expected: compile/import succeed, both retired paths are absent, and all selected tests pass.

- [ ] **Step 2: Start the service and check runtime endpoints**

Start one worker in the `asr` environment:

```bash
cd asr_offline
conda run -n asr python -m uvicorn app.main:app \
  --host 127.0.0.1 --port 8083 --workers 1
```

Check `/get_status` (or the documented health/readiness route), OpenAPI, and POST both
retired routes to verify 404. Stop the process cleanly after checks.

- [ ] **Step 3: Run real French v1.1.8 inference**

Use the existing fixture without copying it:

```bash
curl --fail-with-body -sS -X POST \
  http://127.0.0.1:8083/v1.1.8/seacraft_asr \
  -F 'audioFile=@/Volumes/Data55/asr测试文件/法语音频.mp3;type=audio/mpeg' \
  -F 'language=fr' \
  -F 'showSpk=true' \
  -F 'showEmotion=true' \
  -F 'showRoleIdentify=true' \
  -F 'wordTimestamps=true' \
  > /tmp/asr-v118-fr-result.json
```

Validate the JSON with a read-only script: HTTP/body success, top-level keys exactly equal
the approved set, non-empty text/segments, `language == "fr"`, `role` and `emotion` are
null, at least one non-empty `segment_words`, monotonic bounded times, at least one positive
speed, and 1/5/10-minute `segment_count` values `8/2/1`. Remove the temporary result after
recording verification output.

- [ ] **Step 4: Run dependency and diff checks**

```bash
cd asr_offline
conda run -n asr python -m pip check
cd ..
git diff --check
git status --short
```

Confirm unrelated user changes remain untouched.

- [ ] **Step 5: Request final code review and record evidence**

Dispatch an independent reviewer against the approved design and all commits. Address each
finding with a failing regression test before changing behavior. Re-run Steps 1–4 after any
fix.

## Scope Boundary

The sibling `ai报告分析课程数据` project still calls `/audio/detect_mandarin` and interprets
null role/emotion as business values. It is outside this workspace change and must be migrated
or retired before deployment. This plan reports that release dependency but does not edit the
sibling project without separate authorization.

import asyncio
import io
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


class TempAudioFileTests(unittest.TestCase):
    def test_upload_filename_is_not_used_for_temp_path(self):
        from app.utils.audio_utils import write_audio_bytes_to_temp_file

        path = None
        unsafe_name = "../codex_unsafe_name.wav"
        expected_bad_path = Path("/codex_unsafe_name.wav")

        try:
            path = write_audio_bytes_to_temp_file(b"audio-bytes", file_name=unsafe_name, suffix="wav")
            resolved = Path(path).resolve()

            self.assertEqual(resolved.parent, Path("/tmp").resolve())
            self.assertNotIn("codex_unsafe_name", resolved.name)
            self.assertEqual(resolved.suffix, ".wav")
            self.assertEqual(resolved.read_bytes(), b"audio-bytes")
        finally:
            for candidate in {path, str(expected_bad_path)}:
                if candidate and os.path.exists(candidate):
                    os.remove(candidate)


class GpuConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_does_not_reacquire_gpu_slot_inside_acquired_slot(self):
        import app.core.concurrency as concurrency

        original_semaphore = concurrency.gpu_semaphore
        original_model_lock = concurrency._model_lock
        original_queued_ids = concurrency._queued_task_ids
        original_add_processing = concurrency.add_processing_task
        original_remove_processing = concurrency.remove_processing_task
        original_add_queued = concurrency.add_queued_task
        original_remove_queued = concurrency.remove_queued_task

        class DummyModel:
            def generate(self):
                return ["ok"]

        try:
            concurrency.gpu_semaphore = asyncio.Semaphore(1)
            concurrency._model_lock = asyncio.Lock()
            concurrency._queued_task_ids = []
            concurrency.add_processing_task = lambda task_id: None
            concurrency.remove_processing_task = lambda task_id: None
            concurrency.add_queued_task = lambda task_id: None
            concurrency.remove_queued_task = lambda task_id: None

            async with concurrency.acquire_gpu_slot(timeout=0.1, task_id="unit-test"):
                result = await asyncio.wait_for(
                    concurrency.generate_with_gpu_lock(DummyModel()),
                    timeout=0.2,
                )

            self.assertEqual(result, ["ok"])
        finally:
            concurrency.gpu_semaphore = original_semaphore
            concurrency._model_lock = original_model_lock
            concurrency._queued_task_ids = original_queued_ids
            concurrency.add_processing_task = original_add_processing
            concurrency.remove_processing_task = original_remove_processing
            concurrency.add_queued_task = original_add_queued
            concurrency.remove_queued_task = original_remove_queued

    async def test_acquire_gpu_slot_uses_configured_default_timeout(self):
        import app.core.concurrency as concurrency

        original_semaphore = concurrency.gpu_semaphore
        original_queued_ids = concurrency._queued_task_ids
        original_cfg = dict(concurrency.settings._cfg)
        original_add_processing = concurrency.add_processing_task
        original_remove_processing = concurrency.remove_processing_task
        original_add_queued = concurrency.add_queued_task
        original_remove_queued = concurrency.remove_queued_task

        async def wait_for_slot():
            async with concurrency.acquire_gpu_slot(task_id="timeout-default"):
                pass

        try:
            concurrency.gpu_semaphore = asyncio.Semaphore(1)
            await concurrency.gpu_semaphore.acquire()
            concurrency._queued_task_ids = []
            concurrency.settings._cfg["gpu_slot_timeout_seconds"] = 0.01
            concurrency.add_processing_task = lambda task_id: None
            concurrency.remove_processing_task = lambda task_id: None
            concurrency.add_queued_task = lambda task_id: None
            concurrency.remove_queued_task = lambda task_id: None

            start = asyncio.get_running_loop().time()
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(wait_for_slot(), timeout=0.5)
            elapsed = asyncio.get_running_loop().time() - start

            self.assertLess(elapsed, 0.2)
        finally:
            if concurrency.gpu_semaphore.locked():
                concurrency.gpu_semaphore.release()
            concurrency.gpu_semaphore = original_semaphore
            concurrency._queued_task_ids = original_queued_ids
            concurrency.settings._cfg.clear()
            concurrency.settings._cfg.update(original_cfg)
            concurrency.add_processing_task = original_add_processing
            concurrency.remove_processing_task = original_remove_processing
            concurrency.add_queued_task = original_add_queued
            concurrency.remove_queued_task = original_remove_queued

    async def test_whisper_generator_is_consumed_inside_model_lock(self):
        import app.core.concurrency as concurrency

        original_model_lock = concurrency._model_lock
        lock_states = []

        class FakeWhisper:
            def transcribe(self):
                def segments():
                    lock_states.append(concurrency._model_lock.locked())
                    yield "bonjour"

                return segments(), {"language": "fr"}

        try:
            concurrency._model_lock = asyncio.Lock()
            segments, info = await concurrency.transcribe_with_gpu_lock(FakeWhisper())

            self.assertEqual(segments, ["bonjour"])
            self.assertEqual(info, {"language": "fr"})
            self.assertEqual(lock_states, [True])
            self.assertFalse(concurrency._model_lock.locked())
        finally:
            concurrency._model_lock = original_model_lock

    async def test_whisper_timeout_keeps_model_lock_until_worker_finishes(self):
        import app.core.concurrency as concurrency
        from fastapi import HTTPException

        real_asyncio = asyncio
        original_asyncio = concurrency.asyncio
        original_model_lock = concurrency._model_lock
        worker_started = threading.Event()
        release_worker = threading.Event()
        model_lock_released = threading.Event()
        recovery_wait_started = threading.Event()
        shield_call_count = 0

        class TrackingLock(asyncio.Lock):
            async def __aexit__(self, exc_type, exc_value, traceback):
                result = await super().__aexit__(exc_type, exc_value, traceback)
                model_lock_released.set()
                return result

        class FastTimeoutAsyncio:
            CancelledError = real_asyncio.CancelledError
            TimeoutError = real_asyncio.TimeoutError
            create_task = staticmethod(real_asyncio.create_task)
            to_thread = staticmethod(real_asyncio.to_thread)

            @staticmethod
            def shield(awaitable):
                nonlocal shield_call_count
                shield_call_count += 1
                if shield_call_count >= 2:
                    recovery_wait_started.set()
                return real_asyncio.shield(awaitable)

            @staticmethod
            async def wait_for(awaitable, timeout):
                started = await real_asyncio.to_thread(worker_started.wait, 0.5)
                if not started:
                    raise AssertionError("Whisper worker did not start")
                raise real_asyncio.TimeoutError

        class SlowWhisper:
            def transcribe(self):
                def segments():
                    worker_started.set()
                    release_worker.wait(timeout=1.0)
                    yield "bonjour"

                return segments(), {"language": "fr"}

        task = None
        try:
            concurrency.asyncio = FastTimeoutAsyncio
            concurrency._model_lock = TrackingLock()
            task = asyncio.create_task(
                concurrency.transcribe_with_gpu_lock(SlowWhisper())
            )

            started = await asyncio.to_thread(worker_started.wait, 0.5)
            self.assertTrue(started)
            recovery_started = await asyncio.to_thread(
                recovery_wait_started.wait,
                0.5,
            )

            self.assertTrue(recovery_started)
            self.assertFalse(model_lock_released.is_set())
            self.assertTrue(concurrency._model_lock.locked())
            self.assertFalse(task.done())
        finally:
            release_worker.set()
            if task is not None:
                with self.assertRaises(HTTPException):
                    await task
            concurrency.asyncio = original_asyncio
            concurrency._model_lock = original_model_lock


class AudioAnalyzeTests(unittest.IsolatedAsyncioTestCase):
    async def test_db_snr_removes_temp_file_when_analysis_raises(self):
        import app.api.routes.audio as audio_route

        class FakeUpload:
            filename = "sample.wav"
            file = io.BytesIO(b"not-a-real-audio-file")

        created_path = {}

        def raising_analyzer(path, window_size_sec):
            created_path["path"] = path
            self.assertTrue(os.path.exists(path))
            raise RuntimeError("analysis failed")

        with patch.object(audio_route, "analyze_audio_auto", side_effect=raising_analyzer):
            with self.assertRaises(RuntimeError):
                await audio_route.audio_analyze(FakeUpload(), time_size=1)

        self.assertIn("path", created_path)
        self.assertFalse(os.path.exists(created_path["path"]))


class StartScriptTests(unittest.TestCase):
    def test_start_script_does_not_force_cuda_launch_blocking(self):
        text = Path("docker/start.sh").read_text(encoding="utf-8")

        self.assertNotIn("export CUDA_LAUNCH_BLOCKING=1", text)


class RequirementsPinningTests(unittest.TestCase):
    def test_python311_linux_runtime_uses_torch26_cuda_pair(self):
        for filename in ("requirements.txt", "requirements-pip.txt"):
            with self.subTest(filename=filename):
                requirements = Path(filename).read_text(encoding="utf-8").splitlines()

                self.assertIn("torch==2.6.0", requirements)
                self.assertIn("torchaudio==2.6.0", requirements)
                self.assertNotIn("torch==2.7.0", requirements)
                self.assertNotIn("torchaudio==2.7.0", requirements)

        dockerfile = Path("docker/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("https://download.pytorch.org/whl/cu124", dockerfile)
        self.assertIn("torch==2.6.0 torchaudio==2.6.0", dockerfile)

    def test_runtime_requirements_are_exactly_pinned(self):
        for filename in ("requirements.txt", "requirements-pip.txt"):
            with self.subTest(filename=filename):
                lines = Path(filename).read_text(encoding="utf-8").splitlines()
                requirement_lines = []
                for raw in lines:
                    line = raw.split("#", 1)[0].strip()
                    if line:
                        requirement_lines.append(line)

                self.assertNotIn("aiofiles", requirement_lines)
                for requirement in requirement_lines:
                    self.assertRegex(
                        requirement,
                        r"^[A-Za-z0-9_.-]+==[^=<>!~]+$",
                        msg=f"{filename} has an unpinned requirement: {requirement}",
                    )

    def test_pyannote_dependency_is_absent(self):
        for filename in ("requirements.txt", "requirements-pip.txt"):
            with self.subTest(filename=filename):
                source = Path(filename).read_text(encoding="utf-8")
                self.assertNotIn("pyannote.audio", source)

    def test_pyannote_runtime_code_is_absent(self):
        models_source = Path("app/core/models.py").read_text(encoding="utf-8")
        config_source = Path("app/core/config.py").read_text(encoding="utf-8")

        self.assertNotIn("PyannotePipeline", models_source)
        self.assertNotIn("_model_speaker", models_source)
        self.assertNotIn("get_speaker_model", models_source)
        self.assertNotIn("pyannote_model_yml", config_source)
        self.assertNotIn("open_mul_spk", config_source)
        self.assertFalse(Path("app/utils/pynanote_speaker.py").exists())

    def test_pyannote_config_keys_are_absent(self):
        source = Path("config.toml").read_text(encoding="utf-8")

        self.assertNotIn("pyannote_model_yml", source)
        self.assertNotIn("open_mul_spk", source)

    def test_dockerfile_has_no_pyannote_model_rewrite(self):
        source = Path("docker/Dockerfile").read_text(encoding="utf-8")

        self.assertNotIn("speaker-diarization-3.1", source)

    def test_dockerignore_excludes_only_retired_model_assets(self):
        source = Path(".dockerignore").read_text(encoding="utf-8")
        entries = {
            line.strip()
            for line in source.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        expected = {
            "model/speaker-diarization-3.1/",
            "model/segmentation-3.0/",
            "model/wespeaker-voxceleb-resnet34-LM/",
            "model/bert-base-chinese/",
            "model/bert_output/",
        }

        self.assertTrue(expected.issubset(entries))
        self.assertNotIn("model/", entries)
        self.assertNotIn("model/*", entries)
        self.assertNotIn("model/**", entries)
        self.assertNotIn("model/**/*", entries)


class ConfigTimeoutTests(unittest.TestCase):
    def test_gpu_slot_timeout_is_declared_in_runtime_configs(self):
        config_files = [Path("config.toml")]
        docker_config = Path("config.docker.toml")
        if docker_config.exists():
            config_files.append(docker_config)

        for path in config_files:
            with self.subTest(filename=str(path)):
                text = path.read_text(encoding="utf-8")
                self.assertIn("gpu_slot_timeout_seconds", text)

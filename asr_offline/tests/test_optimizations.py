import asyncio
import io
import os
import tempfile
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

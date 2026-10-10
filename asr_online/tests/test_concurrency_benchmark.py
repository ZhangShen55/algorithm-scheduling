import argparse
import importlib.util
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import websockets


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "test" / "benchmark_v1_0_3_concurrency.py"
SPEC = importlib.util.spec_from_file_location("benchmark_v1_0_3_concurrency", SCRIPT_PATH)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class ConcurrencyBenchmarkTests(unittest.IsolatedAsyncioTestCase):
    def test_service_streams_conda_output_to_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = argparse.Namespace(
                config=benchmark.PROJECT_ROOT / "config.toml",
                conda="conda",
                conda_env="asr",
                port=8084,
            )
            with patch.object(benchmark.subprocess, "Popen") as popen:
                _, output, _ = benchmark.start_service(args, 1, root)
                command = popen.call_args.args[0]
                output.close()

        self.assertIn("--no-capture-output", command)

    def test_worker_config_is_rewritten_and_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.toml"
            benchmark.write_worker_config(
                benchmark.PROJECT_ROOT / "config.toml", target, 6
            )
            content = target.read_text(encoding="utf-8")

        self.assertIn("sensevoice_correction_workers = 6", content)
        self.assertEqual(content.count("sensevoice_correction_workers ="), 1)

    def test_service_event_summary_calculates_correction_and_rtf_metrics(self):
        events = [
            {"event": "v3_model_inference", "model_name": "online", "inference_ms": 100},
            {"event": "v3_model_inference", "model_name": "vad", "inference_ms": 50},
            {"event": "correction_completed", "inference_ms": 200, "queue_ms": 20},
            {"event": "correction_visible", "visible_ms": 250},
            {"event": "correction_stale"},
        ]
        summary = benchmark.summarize_service_events(events, "", 10, 1)

        self.assertEqual(summary["correction_visible"]["p95"], 0.25)
        self.assertEqual(summary["stale"], 1)
        self.assertEqual(summary["compute_rtf"], 0.035)

    def test_service_event_reader_decodes_telemetry_from_installed_formatter(self):
        telemetry = {
            "name": "correction_visible",
            "visible_ms": 245.0,
            "queue_depth": 2,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "service.log"
            path.write_text(
                json.dumps({"event": json.dumps(telemetry)}) + "\n",
                encoding="utf-8",
            )
            events, _ = benchmark.read_service_events(path, 0)

        self.assertEqual(events[0]["event"], "correction_visible")
        self.assertEqual(events[0]["visible_ms"], 245.0)
        self.assertEqual(events[0]["queue_depth"], 2)

    async def test_point_runner_sends_complete_audio_at_realtime_pace(self):
        async def handler(websocket):
            index = 0
            try:
                async for _ in websocket:
                    index += 1
                    await websocket.send(
                        json.dumps(
                            {
                                "key": "segment-000001",
                                "text": "测试",
                                "finished": False,
                                "bg": 0.0,
                                "ed": round(index * benchmark.CHUNK_SECONDS, 2),
                            },
                            ensure_ascii=False,
                        )
                    )
            except websockets.ConnectionClosed:
                pass

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                wav_path = root / "one-chunk.wav"
                with wave.open(str(wav_path), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(16000)
                    wav.writeframes(b"\x01\x00" * benchmark.CHUNK_SAMPLES)
                args = argparse.Namespace(
                    wav=wav_path,
                    url=f"ws://127.0.0.1:{port}/v1.0.3/seacraft_asr_online",
                    output_dir=root / "output",
                    duration_seconds=benchmark.CHUNK_SECONDS,
                    start_seconds=0,
                    tail_chunks=1,
                    drain_seconds=0.1,
                    correction_workers=1,
                    concurrency=2,
                    run_label="test",
                )
                summary = await benchmark.run_point(args)

            self.assertEqual(summary["complete_clients"], 2)
            self.assertEqual(summary["failed_clients"], 0)
            self.assertEqual(summary["responses"], 4)
            self.assertEqual(summary["protocol_errors"], 0)
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()

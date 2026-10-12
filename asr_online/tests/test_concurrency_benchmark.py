import argparse
import importlib.util
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_listener_pids_only_returns_port_owners(self):
        completed = benchmark.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                'LISTEN 0 2048 127.0.0.1:8084 0.0.0.0:* '
                'users:(("python",pid=1234,fd=59),("python",pid=5678,fd=60))\n'
            ),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            pids = benchmark.listener_pids(8084)

        self.assertEqual(pids, {1234, 5678})

    def test_wait_service_stopped_confirms_listener_and_process_group_release(self):
        with (
            patch.object(benchmark, "listener_pids", side_effect=[{1234}, set()]),
            patch.object(
                benchmark,
                "process_group_stats",
                side_effect=[
                    {"pids": [1234]},
                    {"pids": []},
                ],
            ),
            patch.object(benchmark.time, "sleep", return_value=None),
        ):
            status = benchmark.wait_service_stopped(8084, 1234, timeout=1)

        self.assertEqual(
            status,
            {"port_released": True, "process_group_released": True},
        )

    async def test_wait_ready_ignores_listener_outside_new_process_group(self):
        process = Mock()
        process.poll.return_value = None
        response = Mock(status=200)
        with (
            patch.object(benchmark.urllib.request, "urlopen", return_value=response),
            patch.object(
                benchmark,
                "process_group_stats",
                return_value={"pids": [1234]},
            ),
            patch.object(
                benchmark,
                "listener_pids",
                side_effect=[{9999}, {1234}, {1234}],
            ) as listeners,
            patch.object(benchmark.asyncio, "sleep", return_value=None),
        ):
            ready, reason = await benchmark.wait_ready(
                process,
                1234,
                "ws://127.0.0.1:8084/v1.0.3/seacraft_asr_online",
                5,
            )

        self.assertTrue(ready)
        self.assertIsNone(reason)
        self.assertEqual(listeners.call_count, 3)

    async def test_wait_ready_builds_status_url_for_v1_and_v4(self):
        for version in ("v1.0.1", "v1.0.4"):
            process = Mock()
            process.poll.return_value = None
            response = Mock(status=200)
            with (
                self.subTest(version=version),
                patch.object(
                    benchmark.urllib.request, "urlopen", return_value=response
                ) as urlopen,
                patch.object(
                    benchmark,
                    "process_group_stats",
                    return_value={"pids": [1234]},
                ),
                patch.object(benchmark, "listener_pids", return_value={1234}),
                patch.object(benchmark.asyncio, "sleep", return_value=None),
            ):
                ready, _ = await benchmark.wait_ready(
                    process,
                    1234,
                    f"ws://127.0.0.1:8084/{version}/seacraft_asr_online",
                    5,
                )

            self.assertTrue(ready)
            self.assertEqual(
                urlopen.call_args.args[0], "http://127.0.0.1:8084/get_status"
            )

    def test_service_event_summary_calculates_correction_and_rtf_metrics(self):
        events = [
            {"event": "v3_model_inference", "model_name": "online", "inference_ms": 100},
            {"event": "v3_model_inference", "model_name": "vad", "inference_ms": 50},
            {
                "event": "correction_completed",
                "inference_ms": 200,
                "queue_ms": 20,
                "queue_depth": 2,
                "inflight": 3,
            },
            {"event": "correction_visible", "visible_ms": 250},
            {"event": "finished_visible", "visible_ms": 300},
            {"event": "correction_stale"},
        ]
        summary = benchmark.summarize_service_events(events, "", 10)

        self.assertEqual(summary["correction_visible"]["p95"], 0.25)
        self.assertEqual(summary["finished_visible"]["p95"], 0.3)
        self.assertEqual(summary["stale"], 1)
        self.assertEqual(summary["compute_rtf"], 0.035)
        self.assertEqual(summary["queue_depth_average"], 2.0)
        self.assertEqual(summary["inflight_average"], 3.0)

    def test_service_event_summary_includes_v4_correction_coverage(self):
        events = [
            {
                "event": "v4_model_inference",
                "model_name": "online",
                "queue_ms": 5,
                "inference_ms": 80,
            },
            {"event": "v4_correction_eligible"},
            {"event": "v4_correction_eligible"},
            {"event": "v4_correction_submitted"},
            {"event": "v4_correction_applied"},
            {"event": "v4_correction_fallback"},
            {"event": "v4_correction_skipped"},
            {"event": "v4_correction_stale"},
            {"event": "v4_correction_failed"},
        ]

        summary = benchmark.summarize_service_events(events, "", 1)

        self.assertEqual(
            summary["v4_correction"],
            {
                "eligible": 2,
                "submitted": 1,
                "applied": 1,
                "fallback": 1,
                "skipped": 1,
                "stale": 1,
                "failed": 1,
                "coverage": 0.5,
            },
        )
        self.assertEqual(summary["online_queue"]["count"], 1)

    def test_single_client_correction_positions_include_before_and_after_text(self):
        state = benchmark.ClientState(client_id=0)
        state.finished_records.append(
            {
                "client_id": 0,
                "key": "segment-000001",
                "bg": 1.0,
                "ed": 2.0,
                "online_candidate": "在线文本",
                "final_text": "纠正文本。",
            }
        )
        events = [
            {
                "event": "v4_correction_applied",
                "segment_key": "segment-000001",
            }
        ]

        positions = benchmark.v4_correction_positions(events, [state])

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["online_candidate"], "在线文本")
        self.assertEqual(positions[0]["final_text"], "纠正文本。")

    def test_gpu_stats_reports_each_visible_device(self):
        completed = benchmark.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="1024, 20\n2048, 80\n512, 10\n",
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            stats = benchmark.gpu_stats()

        self.assertEqual(stats["memory_mib"], 3584)
        self.assertEqual(stats["utilization_percent"], 80)
        self.assertEqual([item["index"] for item in stats["gpu_devices"]], [0, 1, 2])

    def test_boundary_retest_is_never_shorter_than_ten_minutes(self):
        self.assertEqual(
            benchmark.boundary_retest_duration(
                argparse.Namespace(retest_duration_seconds=None)
            ),
            600.0,
        )
        self.assertEqual(
            benchmark.boundary_retest_duration(
                argparse.Namespace(retest_duration_seconds=30)
            ),
            600.0,
        )

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
                    response = {
                        "key": "segment-000001",
                        "text": "测试",
                        "finished": False,
                        "bg": 0.0,
                        "ed": round(index * benchmark.CHUNK_SECONDS, 2),
                    }
                    await websocket.send(json.dumps(response, ensure_ascii=False))
                    response["text"] = "测试。"
                    await websocket.send(json.dumps(response, ensure_ascii=False))
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
            self.assertEqual(summary["responses"], 8)
            self.assertEqual(summary["partial_latency"]["count"], 4)
            self.assertEqual(summary["protocol_errors"], 0)
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()

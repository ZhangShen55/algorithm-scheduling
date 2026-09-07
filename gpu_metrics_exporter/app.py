from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import pynvml
except ImportError:  # pragma: no cover - exercised by the container image
    pynvml = None  # type: ignore[assignment]


def gpu_snapshot() -> dict[str, object]:
    if pynvml is None:
        raise RuntimeError("pynvml is unavailable")
    pynvml.nvmlInit()
    try:
        devices: list[dict[str, object]] = []
        for index in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
            try:
                temperature = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            except pynvml.NVMLError:
                temperature = None
            try:
                power = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000
            except pynvml.NVMLError:
                power = None
            try:
                process_count = len(pynvml.nvmlDeviceGetComputeRunningProcesses(handle))
            except pynvml.NVMLError:
                process_count = None
            devices.append({
                "index": index,
                "name": name,
                "utilization_percent": utilization.gpu,
                "memory_used_bytes": memory.used,
                "memory_total_bytes": memory.total,
                "temperature_celsius": temperature,
                "power_watts": power,
                "process_count": process_count,
            })
        return {"status": "ok", "sampled_at": time.time(), "devices": devices}
    finally:
        pynvml.nvmlShutdown()


def prometheus(snapshot: dict[str, object]) -> str:
    lines = [
        "# HELP algorithm_gpu_exporter_up Whether NVML was read successfully.",
        "# TYPE algorithm_gpu_exporter_up gauge",
        "algorithm_gpu_exporter_up 1",
    ]
    for device in snapshot["devices"]:
        item = device  # type: ignore[assignment]
        labels = f'gpu_index="{item["index"]}",gpu_name="{str(item["name"]).replace(chr(34), chr(39))}"'
        values = {
            "algorithm_gpu_utilization_percent": item["utilization_percent"],
            "algorithm_gpu_memory_used_bytes": item["memory_used_bytes"],
            "algorithm_gpu_memory_total_bytes": item["memory_total_bytes"],
            "algorithm_gpu_temperature_celsius": item["temperature_celsius"],
            "algorithm_gpu_power_watts": item["power_watts"],
            "algorithm_gpu_process_count": item["process_count"],
        }
        for metric, value in values.items():
            if value is not None:
                lines.append(f"{metric}{{{labels}}} {value}")
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    server_version = "algorithm-gpu-exporter/0.1"

    def _headers(self, content_type: str) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Accept, Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", content_type)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._headers("text/plain")
        self.end_headers()

    def do_GET(self) -> None:
        try:
            if self.path == "/health":
                body = json.dumps({"status": "ok"}).encode()
                content_type = "application/json"
            elif self.path == "/metrics":
                body = prometheus(gpu_snapshot()).encode()
                content_type = "text/plain; version=0.0.4"
            elif self.path == "/gpu":
                body = json.dumps(gpu_snapshot()).encode()
                content_type = "application/json"
            else:
                self.send_error(404)
                return
        except Exception as exc:  # noqa: BLE001 - HTTP boundary must report NVML failures as JSON
            body = json.dumps({"status": "unavailable", "error": str(exc)}).encode()
            content_type = "application/json"
            self.send_response(503)
            self._headers(content_type)
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self._headers(content_type)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    port = int(os.environ.get("GPU_EXPORTER_PORT", "9400"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()

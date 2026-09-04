from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class VisionPipelineMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self._command_pending = Gauge(
            "algorithm_vision_command_pending",
            "Pending visual commands prefetched from Kafka.",
            registry=registry,
        )
        self._command_in_flight = Gauge(
            "algorithm_vision_command_in_flight",
            "Visual course commands currently executing.",
            registry=registry,
        )
        self._command_slot_utilization = Gauge(
            "algorithm_vision_command_slot_utilization_ratio",
            "Ratio of occupied visual course command slots.",
            registry=registry,
        )
        self._media_pending = Gauge(
            "algorithm_vision_media_pending",
            "Media jobs waiting for an ffmpeg or ffprobe process slot.",
            registry=registry,
        )
        self._media_running = Gauge(
            "algorithm_vision_media_running",
            "Active ffmpeg and ffprobe processes.",
            registry=registry,
        )
        self._first_batch_wait = Histogram(
            "algorithm_vision_first_frame_batch_wait_seconds",
            "Time from scan start until the first frame batch is ready.",
            ("stream",),
            registry=registry,
        )
        self._first_vbas_request = Histogram(
            "algorithm_vision_first_vbas_request_delay_seconds",
            "Time from scan start until the first VBas batch request starts.",
            ("stream",),
            registry=registry,
        )
        self._batch_events = Counter(
            "algorithm_vision_batch_events",
            "Prepared and inferred visual batch events.",
            ("stream", "phase"),
            registry=registry,
        )

    def set_command_counts(self, *, pending: int, in_flight: int, limit: int) -> None:
        self._command_pending.set(pending)
        self._command_in_flight.set(in_flight)
        self._command_slot_utilization.set(in_flight / limit if limit > 0 else 0)

    def set_media_counts(self, *, pending: int, running: int) -> None:
        self._media_pending.set(pending)
        self._media_running.set(running)

    def observe_first_batch_wait(self, stream: str, seconds: float) -> None:
        self._first_batch_wait.labels(stream=stream).observe(max(0.0, seconds))

    def observe_first_vbas_request(self, stream: str, seconds: float) -> None:
        self._first_vbas_request.labels(stream=stream).observe(max(0.0, seconds))

    def record_batch(self, stream: str, phase: str) -> None:
        self._batch_events.labels(stream=stream, phase=phase).inc()

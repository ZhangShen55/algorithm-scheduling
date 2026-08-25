from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from scripts.extreme_load.core import NorthboundTargets, ReproducibleIdentity, ResultCategory
from scripts.extreme_load.realtime_asr import (
    ASR_ONLINE_CHUNK_BYTES,
    ASR_ONLINE_CHUNK_DURATION_SECONDS,
    ASR_ONLINE_CHUNK_SAMPLES,
    ASR_ONLINE_TAIL_SILENCE_CHUNKS,
    AsrSessionResult,
    AudioStreamFixture,
    RealtimeAsrRunner,
    _is_finished_message,
    build_asr_online_fixture,
    build_reconnect_specs,
    build_session_specs,
    realtime_asr_session_ladder,
    realtime_asr_session_tiers,
)

TARGETS = NorthboundTargets(
    control_origin="http://192.168.29.11:18100",
    gateway_origin="http://192.168.29.11:18103",
)


class FakeSocket:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.sent: list[bytes | str] = []
        self.recv_count = 0

    async def send(self, message: bytes | str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        self.recv_count += 1
        if self.recv_count == 1:
            return self.response_text
        raise StopAsyncIteration


class FakeClock:
    def __init__(self, *, sleep_overshoot_seconds: float = 0.0) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        self.sleep_overshoot_seconds = sleep_overshoot_seconds

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds + self.sleep_overshoot_seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_audio_is_sent_by_real_time_chunk_size_and_session_text_is_isolated() -> None:
    sockets: dict[str, FakeSocket] = {}
    sleeps: list[float] = []

    @asynccontextmanager
    async def connect(url: str, headers: dict[str, str]):
        assert url == "ws://192.168.29.11:18103/api/online/asr/stream"
        trace_id = headers["X-Trace-ID"]
        socket = FakeSocket(json.dumps({"text": f"subtitle-{trace_id}", "finished": False}))
        sockets[trace_id] = socket
        yield socket

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    fixture = build_asr_online_fixture(
        pcm=b"a" * (ASR_ONLINE_CHUNK_BYTES * 2),
        sample_rate_hz=16000,
        sample_width_bytes=2,
        channels=1,
    )
    runner = RealtimeAsrRunner(TARGETS, connect=connect, sleep=fake_sleep)
    specs = build_session_specs(
        ReproducibleIdentity("campaign-asr", 1), "ASR-REALTIME", session_count=2
    )
    results = await runner.run_sessions(specs, fixture)

    assert all(result.category is ResultCategory.SUCCESS for result in results)
    assert ASR_ONLINE_CHUNK_SAMPLES == 7680
    assert ASR_ONLINE_CHUNK_DURATION_SECONDS == 0.48
    assert ASR_ONLINE_CHUNK_BYTES == 15360
    assert all(
        len(chunk) == ASR_ONLINE_CHUNK_BYTES
        for socket in sockets.values()
        for chunk in socket.sent
    )
    assert all(
        socket.sent[-ASR_ONLINE_TAIL_SILENCE_CHUNKS:]
        == [bytes(ASR_ONLINE_CHUNK_BYTES)] * ASR_ONLINE_TAIL_SILENCE_CHUNKS
        for socket in sockets.values()
    )
    assert len(sleeps) == 2 * (2 + ASR_ONLINE_TAIL_SILENCE_CHUNKS - 1)
    assert all(result.sent_chunk_count == 8 for result in results)
    assert all(result.sent_media_chunk_count == 2 for result in results)
    assert all(result.sent_tail_silence_chunk_count == 6 for result in results)
    assert results[0].trace_id != results[1].trace_id
    assert results[0].message_digests != results[1].message_digests


@pytest.mark.asyncio
async def test_capacity_message_is_classified_as_overload() -> None:
    @asynccontextmanager
    async def connect(url: str, headers: dict[str, str]):
        del url, headers
        yield FakeSocket(json.dumps({"code": 50301, "message": "capacity"}))

    fixture = AudioStreamFixture(
        pcm=b"a" * 3200,
        sample_rate_hz=16000,
        sample_width_bytes=2,
        channels=1,
        chunk_duration_seconds=0.1,
    )
    result = (
        await RealtimeAsrRunner(
            TARGETS,
            connect=connect,
            tail_silence_chunk_count=0,
        ).run_sessions(
            build_session_specs(ReproducibleIdentity("campaign", 1), "ASR", 1), fixture
        )
    )[0]

    assert result.category is ResultCategory.OVERLOAD


@pytest.mark.asyncio
async def test_capacity_message_stops_following_media_and_silence_chunks() -> None:
    socket = FakeSocket(json.dumps({"code": 50301, "message": "capacity"}))

    @asynccontextmanager
    async def connect(url: str, headers: dict[str, str]):
        del url, headers
        yield socket

    clock = FakeClock()
    fixture = build_asr_online_fixture(
        pcm=b"a" * (ASR_ONLINE_CHUNK_BYTES * 4),
        sample_rate_hz=16_000,
        sample_width_bytes=2,
        channels=1,
    )
    result = (
        await RealtimeAsrRunner(
            TARGETS,
            connect=connect,
            sleep=clock.sleep,
            clock=clock,
        ).run_sessions(
            build_session_specs(ReproducibleIdentity("campaign", 1), "ASR", 1),
            fixture,
        )
    )[0]

    assert result.category is ResultCategory.OVERLOAD
    assert result.sent_chunk_count == 1
    assert result.sent_media_chunk_count == 1
    assert result.sent_tail_silence_chunk_count == 0
    assert result.chunk_counts_consistent


@pytest.mark.asyncio
async def test_receiver_failure_after_finished_message_is_not_swallowed() -> None:
    class FailingReceiverSocket(FakeSocket):
        async def recv(self) -> str:
            self.recv_count += 1
            if self.recv_count == 1:
                return json.dumps({"text": "完整语句", "finished": True})
            raise RuntimeError("response channel failed")

    socket = FailingReceiverSocket("")

    @asynccontextmanager
    async def connect(url: str, headers: dict[str, str]):
        del url, headers
        yield socket

    fixture = build_asr_online_fixture(
        pcm=b"a" * (ASR_ONLINE_CHUNK_BYTES * 2),
        sample_rate_hz=16_000,
        sample_width_bytes=2,
        channels=1,
    )
    result = (
        await RealtimeAsrRunner(TARGETS, connect=connect).run_sessions(
            build_session_specs(ReproducibleIdentity("campaign", 1), "ASR", 1),
            fixture,
        )
    )[0]

    assert result.finished_message_count == 1
    assert result.category is ResultCategory.CONNECTION_FAILURE


@pytest.mark.asyncio
async def test_runner_keeps_a_bounded_window_for_the_last_finished_message() -> None:
    class DelayedFinalSocket(FakeSocket):
        async def recv(self) -> str:
            self.recv_count += 1
            if self.recv_count == 1:
                return json.dumps({"text": "进行中", "finished": False})
            if self.recv_count == 2:
                await asyncio.sleep(0.01)
                return json.dumps({"text": "完成", "finished": True})
            raise StopAsyncIteration

    @asynccontextmanager
    async def connect(url: str, headers: dict[str, str]):
        del url, headers
        yield DelayedFinalSocket("")

    fixture = AudioStreamFixture(
        pcm=b"a" * 3200,
        sample_rate_hz=16000,
        sample_width_bytes=2,
        channels=1,
        chunk_duration_seconds=0.1,
    )
    result = (
        await RealtimeAsrRunner(
            TARGETS,
            connect=connect,
            tail_silence_chunk_count=0,
            final_response_grace_seconds=0.1,
        ).run_sessions(
            build_session_specs(ReproducibleIdentity("campaign", 1), "ASR", 1),
            fixture,
        )
    )[0]

    assert result.category is ResultCategory.SUCCESS
    assert len(result.message_digests) == 2
    assert result.finished_message_count == 1


@pytest.mark.asyncio
async def test_runner_pads_last_media_chunk_and_sends_bounded_tail_silence() -> None:
    class SilenceTriggeredSocket(FakeSocket):
        async def recv(self) -> str:
            while len(self.sent) < 3:
                await asyncio.sleep(0)
            if self.recv_count == 0:
                self.recv_count += 1
                return json.dumps({"text": "完整语句", "finished": True})
            raise StopAsyncIteration

    socket = SilenceTriggeredSocket("")

    @asynccontextmanager
    async def connect(url: str, headers: dict[str, str]):
        del url, headers
        yield socket

    async def fake_sleep(_seconds: float) -> None:
        await asyncio.sleep(0)

    fixture = build_asr_online_fixture(
        pcm=b"a" * (ASR_ONLINE_CHUNK_BYTES + 2),
        sample_rate_hz=16000,
        sample_width_bytes=2,
        channels=1,
    )
    result = (
        await RealtimeAsrRunner(
            TARGETS,
            connect=connect,
            sleep=fake_sleep,
            tail_silence_chunk_count=2,
            final_response_grace_seconds=0.1,
        ).run_sessions(
            build_session_specs(ReproducibleIdentity("campaign", 1), "ASR", 1),
            fixture,
        )
    )[0]

    assert len(socket.sent) == 4
    assert socket.sent[0] == b"a" * ASR_ONLINE_CHUNK_BYTES
    assert socket.sent[1] == b"aa" + bytes(ASR_ONLINE_CHUNK_BYTES - 2)
    assert socket.sent[2:] == [bytes(ASR_ONLINE_CHUNK_BYTES)] * 2
    assert result.sent_media_chunk_count == 2
    assert result.sent_tail_silence_chunk_count == 2
    assert result.finished_message_count == 1


@pytest.mark.asyncio
async def test_send_counts_only_include_chunks_completed_before_disconnect() -> None:
    class DisconnectingSocket(FakeSocket):
        async def send(self, message: bytes | str) -> None:
            self.sent.append(message)
            if len(self.sent) == 3:
                raise ConnectionError("upstream closed")

    socket = DisconnectingSocket("")

    @asynccontextmanager
    async def connect(url: str, headers: dict[str, str]):
        del url, headers
        yield socket

    async def fake_sleep(_seconds: float) -> None:
        await asyncio.sleep(0)

    fixture = build_asr_online_fixture(
        pcm=b"a" * (ASR_ONLINE_CHUNK_BYTES * 2),
        sample_rate_hz=16_000,
        sample_width_bytes=2,
        channels=1,
    )
    result = (
        await RealtimeAsrRunner(
            TARGETS,
            connect=connect,
            sleep=fake_sleep,
        ).run_sessions(
            build_session_specs(ReproducibleIdentity("campaign", 1), "ASR", 1),
            fixture,
        )
    )[0]

    assert result.category is ResultCategory.CONNECTION_FAILURE
    assert result.sent_chunk_count == 2
    assert result.sent_media_chunk_count == 2
    assert result.sent_tail_silence_chunk_count == 0
    assert result.chunk_counts_consistent


@pytest.mark.asyncio
async def test_unexpected_receiver_cancellation_is_a_connection_failure() -> None:
    class CancelledReceiverSocket(FakeSocket):
        async def recv(self) -> str:
            raise asyncio.CancelledError

    socket = CancelledReceiverSocket("")

    @asynccontextmanager
    async def connect(url: str, headers: dict[str, str]):
        del url, headers
        yield socket

    fixture = build_asr_online_fixture(
        pcm=b"a" * ASR_ONLINE_CHUNK_BYTES,
        sample_rate_hz=16_000,
        sample_width_bytes=2,
        channels=1,
    )
    result = (
        await RealtimeAsrRunner(
            TARGETS,
            connect=connect,
            tail_silence_chunk_count=0,
        ).run_sessions(
            build_session_specs(ReproducibleIdentity("campaign", 1), "ASR", 1),
            fixture,
        )
    )[0]

    assert result.category is ResultCategory.CONNECTION_FAILURE
    assert result.chunk_counts_consistent


@pytest.mark.asyncio
async def test_parent_cancellation_propagates_from_session_runner() -> None:
    send_started = asyncio.Event()
    never = asyncio.Event()

    class BlockingSocket(FakeSocket):
        async def send(self, message: bytes | str) -> None:
            del message
            send_started.set()
            await never.wait()

        async def recv(self) -> str:
            await never.wait()
            raise AssertionError("unreachable")

    socket = BlockingSocket("")

    @asynccontextmanager
    async def connect(url: str, headers: dict[str, str]):
        del url, headers
        yield socket

    fixture = build_asr_online_fixture(
        pcm=b"a" * ASR_ONLINE_CHUNK_BYTES,
        sample_rate_hz=16_000,
        sample_width_bytes=2,
        channels=1,
    )
    running = asyncio.create_task(
        RealtimeAsrRunner(
            TARGETS,
            connect=connect,
            tail_silence_chunk_count=0,
        ).run_sessions(
            build_session_specs(ReproducibleIdentity("campaign", 1), "ASR", 1),
            fixture,
        )
    )
    await send_started.wait()
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running


@pytest.mark.asyncio
async def test_absolute_deadlines_do_not_accumulate_send_delay() -> None:
    clock = FakeClock()

    class DelayedSendSocket(FakeSocket):
        async def send(self, message: bytes | str) -> None:
            self.sent.append(message)
            clock.advance(0.1)

    socket = DelayedSendSocket(json.dumps({"finished": False}))

    @asynccontextmanager
    async def connect(url: str, headers: dict[str, str]):
        del url, headers
        yield socket

    fixture = build_asr_online_fixture(
        pcm=b"a" * (ASR_ONLINE_CHUNK_BYTES * 3),
        sample_rate_hz=16_000,
        sample_width_bytes=2,
        channels=1,
    )
    result = (
        await RealtimeAsrRunner(
            TARGETS,
            connect=connect,
            sleep=clock.sleep,
            clock=clock,
            tail_silence_chunk_count=0,
        ).run_sessions(
            build_session_specs(ReproducibleIdentity("campaign", 1), "ASR", 1),
            fixture,
        )
    )[0]

    assert clock.sleeps == pytest.approx([0.38, 0.38])
    assert result.category is ResultCategory.SUCCESS
    assert result.max_positive_schedule_drift_seconds == pytest.approx(0.0)
    assert result.planned_media_duration_seconds == pytest.approx(1.44)
    assert result.sent_media_duration_seconds == pytest.approx(1.44)
    assert result.send_elapsed_seconds == pytest.approx(1.06)
    assert result.realtime_factor == pytest.approx(1.06 / 0.96)
    assert result.chunk_counts_consistent


@pytest.mark.asyncio
async def test_excessive_schedule_drift_is_a_load_generator_failure() -> None:
    clock = FakeClock(sleep_overshoot_seconds=0.6)
    socket = FakeSocket(json.dumps({"finished": False}))

    @asynccontextmanager
    async def connect(url: str, headers: dict[str, str]):
        del url, headers
        yield socket

    fixture = build_asr_online_fixture(
        pcm=b"a" * (ASR_ONLINE_CHUNK_BYTES * 3),
        sample_rate_hz=16_000,
        sample_width_bytes=2,
        channels=1,
    )
    result = (
        await RealtimeAsrRunner(
            TARGETS,
            connect=connect,
            sleep=clock.sleep,
            clock=clock,
            tail_silence_chunk_count=0,
            max_schedule_drift_seconds=0.48,
        ).run_sessions(
            build_session_specs(ReproducibleIdentity("campaign", 1), "ASR", 1),
            fixture,
        )
    )[0]

    assert result.category is ResultCategory.LOAD_GENERATOR_FAILURE
    assert result.sent_chunk_count == 1
    assert result.max_positive_schedule_drift_seconds == pytest.approx(0.6)
    assert result.chunk_counts_consistent


@pytest.mark.parametrize(
    ("sample_rate_hz", "sample_width_bytes", "channels"),
    ((8_000, 2, 1), (16_000, 1, 1), (16_000, 2, 2)),
)
def test_asr_online_fixture_rejects_non_contract_pcm(
    sample_rate_hz: int,
    sample_width_bytes: int,
    channels: int,
) -> None:
    with pytest.raises(ValueError, match="16 kHz 单声道 signed 16-bit PCM"):
        build_asr_online_fixture(
            pcm=b"\x00\x00" * ASR_ONLINE_CHUNK_SAMPLES,
            sample_rate_hz=sample_rate_hz,
            sample_width_bytes=sample_width_bytes,
            channels=channels,
        )


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        ('{"finished":true}', True),
        ('{"finished":false}', False),
        ('{"finished":"true"}', False),
        ('{"finished":1}', False),
        ("not-json", False),
    ),
)
def test_finished_message_requires_the_literal_json_boolean(
    message: str,
    expected: bool,
) -> None:
    assert _is_finished_message(message) is expected


@pytest.mark.parametrize("count", (-1, 13, 1.5, True))
def test_runner_rejects_unbounded_or_non_integer_tail_silence(count: object) -> None:
    with pytest.raises(ValueError, match="0 到 12"):
        RealtimeAsrRunner(TARGETS, tail_silence_chunk_count=count)  # type: ignore[arg-type]


@pytest.mark.parametrize("seconds", (0.0, -1.0, float("inf"), float("nan")))
def test_runner_rejects_unbounded_schedule_drift_threshold(seconds: float) -> None:
    with pytest.raises(ValueError, match="max_schedule_drift_seconds"):
        RealtimeAsrRunner(TARGETS, max_schedule_drift_seconds=seconds)


@pytest.mark.parametrize("seconds", (0.0, -1.0, float("inf"), float("nan")))
@pytest.mark.parametrize(
    "field_name",
    ("session_timeout_seconds", "final_response_grace_seconds"),
)
def test_runner_rejects_unbounded_timeouts(field_name: str, seconds: float) -> None:
    with pytest.raises(ValueError, match=field_name):
        RealtimeAsrRunner(TARGETS, **{field_name: seconds})


def test_realtime_session_tiers_include_capacity_and_overload_levels() -> None:
    assert realtime_asr_session_tiers() == (1, 10, 24, 30, 60, 90, 150)
    ladder = realtime_asr_session_ladder()
    assert [level.session_count for level in ladder if level.within_declared_capacity] == [
        1,
        10,
        24,
        30,
    ]


def test_interrupted_sessions_get_distinct_reconnect_identity() -> None:
    identity = ReproducibleIdentity("campaign-asr", 1)
    originals = build_session_specs(identity, "ASR-INTERRUPT", 2)
    reconnects = build_reconnect_specs(identity, "ASR-INTERRUPT", originals)

    assert [item.reconnect_of for item in reconnects] == [
        item.session_id for item in originals
    ]
    assert not ({item.session_id for item in reconnects} & {item.session_id for item in originals})
    assert not ({item.trace_id for item in reconnects} & {item.trace_id for item in originals})


def test_audio_fixture_rejects_non_pcm_aligned_content() -> None:
    with pytest.raises(ValueError, match="对齐"):
        AudioStreamFixture(
            pcm=b"abc",
            sample_rate_hz=16000,
            sample_width_bytes=2,
            channels=1,
            chunk_duration_seconds=0.1,
        )


def test_session_result_exposes_chunk_count_inconsistency() -> None:
    result = AsrSessionResult(
        session_id="session-1",
        trace_id="trace-1",
        category=ResultCategory.SUCCESS,
        sent_chunk_count=3,
        message_digests=(),
        sent_media_chunk_count=1,
        sent_tail_silence_chunk_count=1,
    )

    assert not result.chunk_counts_consistent

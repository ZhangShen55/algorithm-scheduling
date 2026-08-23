from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from scripts.extreme_load.core import NorthboundTargets, ReproducibleIdentity, ResultCategory
from scripts.extreme_load.realtime_asr import (
    AudioStreamFixture,
    RealtimeAsrRunner,
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

    fixture = AudioStreamFixture(
        pcm=b"a" * 6400,
        sample_rate_hz=16000,
        sample_width_bytes=2,
        channels=1,
        chunk_duration_seconds=0.1,
    )
    runner = RealtimeAsrRunner(TARGETS, connect=connect, sleep=fake_sleep)
    specs = build_session_specs(
        ReproducibleIdentity("campaign-asr", 1), "ASR-REALTIME", session_count=2
    )
    results = await runner.run_sessions(specs, fixture)

    assert all(result.category is ResultCategory.SUCCESS for result in results)
    assert all(len(chunk) == 3200 for socket in sockets.values() for chunk in socket.sent)
    assert len(sleeps) == 2
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
        await RealtimeAsrRunner(TARGETS, connect=connect).run_sessions(
            build_session_specs(ReproducibleIdentity("campaign", 1), "ASR", 1), fixture
        )
    )[0]

    assert result.category is ResultCategory.OVERLOAD


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
            final_response_grace_seconds=0.1,
        ).run_sessions(
            build_session_specs(ReproducibleIdentity("campaign", 1), "ASR", 1),
            fixture,
        )
    )[0]

    assert result.category is ResultCategory.SUCCESS
    assert len(result.message_digests) == 2
    assert result.finished_message_count == 1


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

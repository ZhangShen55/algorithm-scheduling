from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from packages.platform_common.lease_resilience import (
    ControlDeterministicFailureError,
    ControlTransientFailureError,
    LeaseCapacityUnavailableError,
    LeaseLostError,
    LeaseProtocolError,
    LeaseRenewalExhaustedError,
    LeaseRenewalPolicy,
    classify_lease_response,
    classify_lease_transport_error,
    release_lease_with_retry,
    remaining_deadline_seconds,
    renew_lease_with_retry,
    wait_for_retry,
)


@pytest.mark.asyncio
async def test_first_read_error_retries_same_lease_and_recovers() -> None:
    calls = 0
    request = httpx.Request("POST", "http://control/lease/renew")

    async def renew() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadError("响应读取失败", request=request)
        return "renewed"

    result = await renew_lease_with_retry(
        lease_id="lease-001",
        confirmed_expires_at=datetime.now(UTC) + timedelta(seconds=60),
        renew=renew,
        policy=LeaseRenewalPolicy(
            max_attempts=3,
            base_delay_seconds=0,
            max_delay_seconds=0,
            safety_margin_seconds=5,
        ),
    )

    assert result == "renewed"
    assert calls == 2


@pytest.mark.asyncio
async def test_retry_stops_when_safety_window_is_exhausted() -> None:
    request = httpx.Request("POST", "http://control/lease/renew")

    async def renew() -> str:
        raise httpx.ReadError("持续读取失败", request=request)

    with pytest.raises(LeaseRenewalExhaustedError):
        await renew_lease_with_retry(
            lease_id="lease-expiring",
            confirmed_expires_at=datetime.now(UTC) + timedelta(seconds=1),
            renew=renew,
            policy=LeaseRenewalPolicy(
                max_attempts=5,
                base_delay_seconds=0,
                max_delay_seconds=0,
                safety_margin_seconds=2,
            ),
        )


@pytest.mark.asyncio
async def test_explicit_404_is_confirmed_lease_loss() -> None:
    request = httpx.Request("POST", "http://control/lease/renew")
    response = httpx.Response(404, request=request)

    async def renew() -> str:
        raise httpx.HTTPStatusError("missing", request=request, response=response)

    with pytest.raises(LeaseLostError):
        await renew_lease_with_retry(
            lease_id="lease-missing",
            confirmed_expires_at=datetime.now(UTC) + timedelta(seconds=60),
            renew=renew,
            policy=LeaseRenewalPolicy(),
        )


@pytest.mark.asyncio
async def test_protocol_error_is_not_retried() -> None:
    calls = 0

    async def renew() -> str:
        nonlocal calls
        calls += 1
        raise ValueError("lease_id 不一致")

    with pytest.raises(LeaseProtocolError):
        await renew_lease_with_retry(
            lease_id="lease-invalid",
            confirmed_expires_at=datetime.now(UTC) + timedelta(seconds=60),
            renew=renew,
            policy=LeaseRenewalPolicy(),
        )

    assert calls == 1


@pytest.mark.asyncio
async def test_expired_safety_window_does_not_send_another_renewal() -> None:
    calls = 0

    async def renew() -> str:
        nonlocal calls
        calls += 1
        return "must-not-run"

    with pytest.raises(LeaseRenewalExhaustedError):
        await renew_lease_with_retry(
            lease_id="lease-expired",
            confirmed_expires_at=datetime.now(UTC),
            renew=renew,
            policy=LeaseRenewalPolicy(safety_margin_seconds=0),
        )

    assert calls == 0


@pytest.mark.asyncio
async def test_release_response_loss_retries_same_lease_and_404_is_success() -> None:
    calls = 0
    request = httpx.Request("POST", "http://control/release")

    async def release() -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadError("响应丢失", request=request)
        return httpx.Response(404, request=request)

    released = await release_lease_with_retry(
        lease_id="lease-release-001",
        release=release,
        policy=LeaseRenewalPolicy(
            max_attempts=2,
            base_delay_seconds=0,
            max_delay_seconds=0,
        ),
    )

    assert released is True
    assert calls == 2


@pytest.mark.asyncio
async def test_renewal_cancellation_is_not_converted_to_protocol_error() -> None:
    started = asyncio.Event()

    async def renew() -> str:
        started.set()
        await asyncio.sleep(60)
        return "never"

    task = asyncio.create_task(
        renew_lease_with_retry(
            lease_id="lease-cancelled",
            confirmed_expires_at=datetime.now(UTC) + timedelta(seconds=60),
            renew=renew,
            policy=LeaseRenewalPolicy(),
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("status_code", (502, 503, 504))
def test_control_service_errors_are_classified_as_transient(status_code: int) -> None:
    request = httpx.Request("POST", "http://control/lease")
    response = httpx.Response(status_code, request=request)

    with pytest.raises(ControlTransientFailureError):
        classify_lease_response(
            response,
            capability="person_count",
            capacity_unavailable=False,
        )


def test_capacity_unavailable_has_distinct_type() -> None:
    request = httpx.Request("POST", "http://control/lease")
    response = httpx.Response(503, request=request)

    with pytest.raises(LeaseCapacityUnavailableError):
        classify_lease_response(
            response,
            capability="person_count",
            capacity_unavailable=True,
        )


def test_deterministic_control_error_is_not_transient() -> None:
    request = httpx.Request("POST", "http://control/lease")
    response = httpx.Response(409, request=request)

    with pytest.raises(ControlDeterministicFailureError):
        classify_lease_response(
            response,
            capability="person_count",
            capacity_unavailable=False,
        )


def test_control_connect_error_is_classified_without_using_message_text() -> None:
    request = httpx.Request("POST", "http://control/lease")

    first = classify_lease_transport_error(
        httpx.ConnectError("", request=request)
    )
    second = classify_lease_transport_error(
        httpx.ReadTimeout("任意文本", request=request)
    )

    assert isinstance(first, ControlTransientFailureError)
    assert isinstance(second, ControlTransientFailureError)


@pytest.mark.asyncio
async def test_backoff_obeys_monotonic_deadline() -> None:
    deadline = asyncio.get_running_loop().time() + 0.02

    assert await wait_for_retry(
        deadline=deadline,
        attempt=8,
        base_delay_seconds=1,
        max_delay_seconds=2,
        jitter_ratio=0,
    ) is False
    assert remaining_deadline_seconds(deadline) == 0

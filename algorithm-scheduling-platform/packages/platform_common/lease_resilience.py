from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar

import httpx

LeaseT = TypeVar("LeaseT")


@dataclass(frozen=True, slots=True)
class LeaseRenewalPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.2
    max_delay_seconds: float = 2.0
    safety_margin_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("租约续租尝试次数必须大于 0")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("租约续租退避时间不能小于 0")
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("租约续租基础退避不能大于最大退避")
        if self.safety_margin_seconds < 0:
            raise ValueError("租约续租安全余量不能小于 0")


class LeaseLostError(RuntimeError):
    pass


class LeaseRenewalExhaustedError(RuntimeError):
    pass


class LeaseProtocolError(RuntimeError):
    pass


def is_transient_lease_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.NetworkError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return False


async def renew_lease_with_retry(
    *,
    lease_id: str,
    confirmed_expires_at: datetime,
    renew: Callable[[], Awaitable[LeaseT]],
    policy: LeaseRenewalPolicy,
    now: Callable[[], datetime] | None = None,
) -> LeaseT:
    current_time = now or (lambda: datetime.now(UTC))
    deadline = confirmed_expires_at - timedelta(
        seconds=policy.safety_margin_seconds
    )
    last_error: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        if current_time() >= deadline:
            break
        try:
            return await renew()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise LeaseLostError(f"容量租约已不存在: {lease_id}") from exc
            if not is_transient_lease_error(exc):
                raise LeaseProtocolError(f"容量租约续租响应不可恢复: {lease_id}") from exc
            last_error = exc
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            last_error = exc
        except (KeyError, TypeError, ValueError) as exc:
            raise LeaseProtocolError(f"容量租约续租响应无效: {lease_id}") from exc

        if attempt >= policy.max_attempts:
            break
        remaining = (deadline - current_time()).total_seconds()
        if remaining <= 0:
            break
        delay = min(
            policy.max_delay_seconds,
            policy.base_delay_seconds * (2 ** (attempt - 1)),
            remaining,
        )
        if delay > 0:
            await asyncio.sleep(delay)

    raise LeaseRenewalExhaustedError(
        f"容量租约续租在安全窗口内未恢复: {lease_id}"
    ) from last_error


async def release_lease_with_retry(
    *,
    lease_id: str,
    release: Callable[[], Awaitable[httpx.Response]],
    policy: LeaseRenewalPolicy,
) -> bool:
    """返回 False 表示结果未确认，调用方应记录并依赖 TTL 回收。"""

    for attempt in range(1, policy.max_attempts + 1):
        try:
            response = await release()
            if response.status_code == 404:
                return True
            response.raise_for_status()
            return True
        except Exception as exc:
            if not is_transient_lease_error(exc):
                raise
            if attempt >= policy.max_attempts:
                return False
            delay = min(
                policy.max_delay_seconds,
                policy.base_delay_seconds * (2 ** (attempt - 1)),
            )
            if delay > 0:
                await asyncio.sleep(delay)
    raise AssertionError("租约释放重试循环不应到达此处")

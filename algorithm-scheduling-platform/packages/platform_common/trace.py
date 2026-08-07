from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from uuid import uuid4

_trace_id: ContextVar[str | None] = ContextVar("platform_trace_id", default=None)


def new_trace_id() -> str:
    return uuid4().hex


def get_trace_id() -> str | None:
    return _trace_id.get()


def bind_trace_id(trace_id: str | None = None) -> Token[str | None]:
    return _trace_id.set(trace_id or new_trace_id())


def reset_trace_id(token: Token[str | None]) -> None:
    _trace_id.reset(token)


@contextmanager
def trace_context(trace_id: str | None = None) -> Iterator[str]:
    token = bind_trace_id(trace_id)
    current = get_trace_id()
    assert current is not None
    try:
        yield current
    finally:
        reset_trace_id(token)

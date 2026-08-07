from __future__ import annotations

from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8))


def format_elapsed(seconds: float) -> str:
    return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m {int(seconds % 60)}s"


def now_ms_text() -> str:
    return str(int(datetime.now(BEIJING_TZ).timestamp() * 1000))

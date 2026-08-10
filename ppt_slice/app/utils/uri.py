"""URI formatting helpers for logs and user-visible errors."""
from urllib.parse import urlsplit, urlunsplit


def redact_uri_for_log(value: str) -> str:
    parts = urlsplit(str(value))
    if not parts.scheme or not parts.netloc:
        return str(value)
    hostname = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))

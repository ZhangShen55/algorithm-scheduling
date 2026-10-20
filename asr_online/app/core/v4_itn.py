"""WeTextProcessing ITN adapter for v1.0.4 correction candidates."""
import asyncio
import json
import logging
import threading
import time


logger = logging.getLogger(__name__)


class V4ITN:
    """Lazy, cached Chinese inverse normalizer with passthrough fallback."""

    def __init__(
        self,
        *,
        enabled=True,
        failure_mode="passthrough",
        remove_interjections=True,
        enable_standalone_number=True,
        enable_0_to_9=False,
        enable_million=False,
    ):
        if failure_mode != "passthrough":
            raise ValueError("v4_itn_failure_mode 目前只支持 passthrough")
        self.enabled = bool(enabled)
        self.failure_mode = failure_mode
        self.options = {
            "remove_interjections": bool(remove_interjections),
            "enable_standalone_number": bool(enable_standalone_number),
            "enable_0_to_9": bool(enable_0_to_9),
            "enable_million": bool(enable_million),
        }
        self._normalizer = None
        self._import_error = None
        self._lock = threading.Lock()

    def _get_normalizer(self):
        if self._normalizer is not None or self._import_error is not None:
            return self._normalizer
        with self._lock:
            if self._normalizer is not None or self._import_error is not None:
                return self._normalizer
            try:
                from itn.chinese.inverse_normalizer import InverseNormalizer

                # The dependency installs its logger while importing; suppress
                # FST construction noise before building the cached normalizer.
                logging.getLogger("wetext").setLevel(logging.WARNING)
                self._normalizer = InverseNormalizer(**self.options)
            except Exception as exc:  # pragma: no cover - platform dependent
                self._import_error = type(exc).__name__
                logger.warning(
                    json.dumps(
                        {
                            "name": "v4_itn_unavailable",
                            "enabled": self.enabled,
                            "error_type": self._import_error,
                        },
                        ensure_ascii=True,
                    )
                )
        return self._normalizer

    def _apply_sync(self, text):
        started = time.monotonic()
        if not self.enabled:
            return text, 0.0, "disabled", None
        normalizer = self._get_normalizer()
        if normalizer is None:
            return text, (time.monotonic() - started) * 1000, "unavailable", self._import_error
        try:
            value = normalizer.normalize(text)
            if not isinstance(value, str):
                raise TypeError("ITN 返回值不是字符串")
            return value, (time.monotonic() - started) * 1000, "applied", None
        except Exception as exc:  # pragma: no cover - dependency/runtime dependent
            error_type = type(exc).__name__
            logger.warning(
                json.dumps(
                    {"name": "v4_itn_fallback", "enabled": self.enabled,
                     "error_type": error_type},
                    ensure_ascii=True,
                )
            )
            return text, (time.monotonic() - started) * 1000, "fallback", error_type

    async def apply(self, text):
        if not text:
            return text, 0.0, "empty", None
        return await asyncio.to_thread(self._apply_sync, text)

    async def warmup(self):
        if self.enabled:
            await asyncio.to_thread(self._get_normalizer)

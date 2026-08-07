from __future__ import annotations

import threading
import time


class AppState:
    def __init__(self) -> None:
        self.start_time = time.time()
        self._request_count = 0
        self._lock = threading.Lock()

    def increment_requests(self) -> int:
        with self._lock:
            self._request_count += 1
            return self._request_count

    @property
    def request_count(self) -> int:
        with self._lock:
            return self._request_count


app_state = AppState()

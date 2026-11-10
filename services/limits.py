"""悪用で外部APIの料金が膨らまないようにする回数の制限。

インスタンスごとにメモリで数えるので、最大インスタンス数の分だけ上限は緩む。
料金を確実に止めるのは GCP のクォータ（docs/gcp-setup.md）で、これはその手前の防御。
"""
import threading
import time

MAX_KEYS = 10_000


class RateLimiter:
    """キーごとに、決まった時間の中の回数を数える。"""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}

    def allow(self, key: str, limit: int, window_s: int) -> bool:
        now = self._clock()
        with self._lock:
            started, count = self._windows.get(key, (now, 0))
            if now - started >= window_s:
                started, count = now, 0
            if count >= limit:
                return False
            if key not in self._windows and len(self._windows) >= MAX_KEYS:
                self._windows.clear()
            self._windows[key] = (started, count + 1)
            return True

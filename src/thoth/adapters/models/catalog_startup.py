"""Refresh the provider model list once at start, in the background.

The refresh never delays the screen or the server. A list fetched within a day is left alone, a
missing or older one is fetched once, each provider at most one at a time, and a fetch that takes
longer than the deadline is given up on. There is no timer: later refreshes are the user's
"load models" button and a finished login.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime, timedelta
from typing import Protocol

from thoth.domain.model_catalog import REFRESH_MAX_AGE_SECONDS


class StartupSource(Protocol):
    provider: str

    def refresh_due(self, now: datetime, max_age: timedelta) -> bool: ...
    def refresh(self) -> None: ...


class SingleFlight:
    """Overlapping callers share one run: the first runs it, the others wait and reuse it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: threading.Event | None = None

    def run(self, work: Callable[[], None]) -> bool:
        """True when this call ran `work`; False when it waited for one already running."""

        with self._lock:
            waiting = self._running
            if waiting is None:
                self._running = threading.Event()
        if waiting is not None:
            waiting.wait()
            return False
        try:
            work()
        finally:
            with self._lock:
                done, self._running = self._running, None
            if done is not None:
                done.set()
        return True


class CatalogStartupRefresher:
    def __init__(
        self,
        sources: Sequence[StartupSource],
        *,
        max_age: timedelta = timedelta(seconds=REFRESH_MAX_AGE_SECONDS),
        deadline_seconds: float = 20.0,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sources, self._max_age = tuple(sources), max_age
        self._deadline, self._now = deadline_seconds, now
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._started = False
        self._pending: list[threading.Event] = []
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, 2 * len(self._sources)), thread_name_prefix="model-catalog"
        )
        self.outcomes: dict[str, str] = {}

    def start(self) -> None:
        """Begin in the background and return at once. Only the first call does anything."""

        with self._lock:
            if self._started or self._closed.is_set():
                return
            self._started = True
        for source in self._sources:
            done = threading.Event()
            self._pending.append(done)
            self._pool.submit(self._one, source, done)

    def wait(self, timeout: float) -> None:
        """For tests and orderly shutdown: wait for the started refreshes to finish or time out."""

        for done in tuple(self._pending):
            done.wait(timeout)

    def _one(self, source: StartupSource, done: threading.Event) -> None:
        try:
            if self._closed.is_set():
                self.outcomes[source.provider] = "CANCELLED"
            elif not source.refresh_due(self._now(), self._max_age):
                self.outcomes[source.provider] = "FRESH"
            else:
                work = self._pool.submit(source.refresh)
                try:
                    work.result(timeout=self._deadline)
                    self.outcomes[source.provider] = "REFRESHED"
                except FutureTimeout:
                    self.outcomes[source.provider] = "TIMED_OUT"
                except Exception:
                    self.outcomes[source.provider] = "FAILED"
        finally:
            done.set()

    def close(self) -> None:
        """Stop starting work and return without waiting for a fetch already in flight."""

        self._closed.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

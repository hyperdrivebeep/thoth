"""Pace of a streaming model reply: a stream that keeps arriving but crawls is stopped locally.

Idle time is already bounded by the read-idle timeout, which any trickle of bytes resets. This
looks at the output events themselves. Nothing is judged before the first output event, so a long
silent reasoning phase is not a stall; the per-call total limit covers that case.

The defaults rest on 27 measured calls:
the slowest normal call averaged 16.9 output events a second, the stalled call 1.4.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from decimal import Decimal
from time import monotonic

from thoth.domain.model_dispatch import TransportTimeouts

CODEX_PACE_DEFAULTS = TransportTimeouts(
    dispatch_total_seconds=Decimal(720),
    stall_min_events_per_second=Decimal(5),
    stall_warmup_seconds=Decimal(120),
    stall_window_seconds=Decimal(60),
    stall_sustain_seconds=Decimal(120),
    # 25,000 output events is about 2.6 times the largest normal call (9,618); 8 visible bytes per
    # requested output token with a 16 KiB floor; 2,000 blank-only events in a row.
    runaway_max_output_events=25_000,
    runaway_visible_bytes_per_token=8,
    runaway_min_visible_bytes=16_384,
    runaway_blank_run=2_000,
    policy_ref="CODEX_STREAM_PACE_V1",
)

_FIELDS = (
    "dispatch_total_seconds",
    "stall_min_events_per_second",
    "stall_warmup_seconds",
    "stall_window_seconds",
    "stall_sustain_seconds",
    "runaway_max_output_events",
    "runaway_visible_bytes_per_token",
    "runaway_min_visible_bytes",
    "runaway_blank_run",
)


def with_pace_defaults(timeouts: TransportTimeouts) -> TransportTimeouts:
    """Fill the pace limits the caller left unset, keeping everything it did set."""

    return timeouts.model_copy(
        update={
            name: getattr(CODEX_PACE_DEFAULTS, name)
            for name in _FIELDS
            if getattr(timeouts, name) is None
        }
    )


class StreamPace:
    def __init__(
        self,
        timeouts: TransportTimeouts,
        clock: Callable[[], float] = monotonic,
        output_tokens: int = 0,
    ) -> None:
        limits = with_pace_defaults(timeouts)
        assert limits.stall_min_events_per_second is not None
        assert limits.stall_warmup_seconds is not None
        assert limits.stall_window_seconds is not None
        assert limits.stall_sustain_seconds is not None
        assert limits.runaway_max_output_events is not None
        assert limits.runaway_visible_bytes_per_token is not None
        assert limits.runaway_min_visible_bytes is not None
        assert limits.runaway_blank_run is not None
        self.min_events_per_second = float(limits.stall_min_events_per_second)
        self.warmup_seconds = float(limits.stall_warmup_seconds)
        self.window_seconds = float(limits.stall_window_seconds)
        self.sustain_seconds = float(limits.stall_sustain_seconds)
        self.dispatch_total_seconds = (
            None if limits.dispatch_total_seconds is None else float(limits.dispatch_total_seconds)
        )
        self._clock = clock
        self._first_at: float | None = None
        self._last_ok_at: float | None = None
        self._buckets: deque[list[float]] = deque()
        self.events = 0
        self.max_output_events = limits.runaway_max_output_events
        self.max_visible_bytes = max(
            max(output_tokens, 0) * limits.runaway_visible_bytes_per_token,
            limits.runaway_min_visible_bytes,
        )
        self.max_blank_run = limits.runaway_blank_run
        self.visible_bytes = 0
        self.blank_run = 0
        self.runaway_limit: str | None = None

    def observe(self, count: int = 1) -> None:
        now = self._clock()
        if self._first_at is None:
            self._first_at = self._last_ok_at = now
        self.events += count
        second = float(int(now))
        if self._buckets and self._buckets[-1][0] == second:
            self._buckets[-1][1] += count
        else:
            self._buckets.append([second, float(count)])

    def observe_output(self, delta: str) -> str | None:
        """Count one output delta; returns the runaway limit it crossed, if any (and records it)."""

        self.observe()
        self.visible_bytes += len(delta.encode())
        self.blank_run = self.blank_run + 1 if not delta.strip() else 0
        if self.runaway_limit is None:
            if self.events > self.max_output_events:
                self.runaway_limit = "OUTPUT_EVENTS"
            elif self.visible_bytes > self.max_visible_bytes:
                self.runaway_limit = "VISIBLE_BYTES"
            elif self.blank_run >= self.max_blank_run:
                self.runaway_limit = "BLANK_RUN"
        return self.runaway_limit

    def window_rate(self) -> float | None:
        if self._first_at is None:
            return None
        now = self._clock()
        while self._buckets and self._buckets[0][0] <= now - self.window_seconds:
            self._buckets.popleft()
        span = max(1.0, min(self.window_seconds, now - self._first_at))
        return sum(count for _, count in self._buckets) / span

    def stalled(self) -> bool:
        """True once the recent pace has stayed under the limit for the sustain time."""

        if self._first_at is None or self._last_ok_at is None:
            return False
        now = self._clock()
        if now - self._first_at <= self.warmup_seconds:
            self._last_ok_at = now
            return False
        rate = self.window_rate()
        if rate is not None and rate >= self.min_events_per_second:
            self._last_ok_at = now
            return False
        return now - self._last_ok_at >= self.sustain_seconds

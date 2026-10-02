"""The model list is refreshed once at start, in the background, only when it is missing or old."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

from thoth.adapters.models.catalog_startup import CatalogStartupRefresher, SingleFlight

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


class Source:
    def __init__(self, provider: str, fetched: datetime | None, *, block: bool = False) -> None:
        self.provider, self.fetched, self.block = provider, fetched, block
        self.refreshes = 0
        self.entered, self.release = threading.Event(), threading.Event()

    def refresh_due(self, now: datetime, max_age: timedelta) -> bool:
        return self.fetched is None or now - self.fetched > max_age

    def refresh(self) -> None:
        self.refreshes += 1
        self.entered.set()
        if self.block:
            self.release.wait(5)


def refresher(*sources: Source, deadline: float = 5.0) -> CatalogStartupRefresher:
    return CatalogStartupRefresher(sources, now=lambda: NOW, deadline_seconds=deadline)


def test_a_list_fetched_within_a_day_is_not_fetched_again() -> None:
    fresh = Source("codex-oauth", NOW - timedelta(hours=23, minutes=59))
    starter = refresher(fresh)
    starter.start()
    starter.wait(2)
    assert fresh.refreshes == 0 and starter.outcomes == {"codex-oauth": "FRESH"}
    starter.close()


def test_a_missing_or_old_list_is_fetched_exactly_once() -> None:
    missing = Source("a", None)
    old = Source("b", NOW - timedelta(hours=25))
    starter = refresher(missing, old)
    starter.start()
    starter.wait(2)
    assert (missing.refreshes, old.refreshes) == (1, 1)
    assert starter.outcomes == {"a": "REFRESHED", "b": "REFRESHED"}
    starter.start()  # a second start in the same run asks nobody again
    starter.wait(2)
    assert (missing.refreshes, old.refreshes) == (1, 1)
    starter.close()


def test_starting_does_not_wait_for_the_provider() -> None:
    slow = Source("codex-oauth", None, block=True)
    starter = refresher(slow)
    began = time.monotonic()
    starter.start()
    assert time.monotonic() - began < 1
    assert slow.entered.wait(2)
    slow.release.set()
    starter.wait(2)
    starter.close()


def test_a_refresh_that_outlasts_its_deadline_is_given_up_on_not_waited_for() -> None:
    stuck = Source("codex-oauth", None, block=True)
    starter = refresher(stuck, deadline=0.2)
    starter.start()
    starter.wait(3)
    assert starter.outcomes == {"codex-oauth": "TIMED_OUT"}
    stuck.release.set()
    starter.close()


def test_a_failing_source_is_recorded_and_does_not_stop_the_others() -> None:
    class Broken(Source):
        def refresh(self) -> None:
            raise RuntimeError("provider down")

    broken, fine = Broken("a", None), Source("b", None)
    starter = refresher(broken, fine)
    starter.start()
    starter.wait(2)
    assert starter.outcomes == {"a": "FAILED", "b": "REFRESHED"} and fine.refreshes == 1
    starter.close()


def test_closing_during_a_refresh_does_not_hang_and_starts_nothing_new() -> None:
    slow = Source("a", None, block=True)
    later = Source("b", None)
    starter = refresher(slow, later)
    starter.start()
    assert slow.entered.wait(2)
    began = time.monotonic()
    starter.close()
    assert time.monotonic() - began < 1
    slow.release.set()
    time.sleep(0.2)
    assert later.refreshes <= 1


def test_overlapping_requests_for_one_provider_share_one_fetch() -> None:
    flight = SingleFlight()
    calls: list[int] = []
    release, entered = threading.Event(), threading.Event()

    def fetch() -> None:
        calls.append(1)
        entered.set()
        release.wait(3)

    results: list[bool] = []
    leader = threading.Thread(target=lambda: results.append(flight.run(fetch)))
    leader.start()
    assert entered.wait(2)
    follower = threading.Thread(target=lambda: results.append(flight.run(fetch)))
    follower.start()
    time.sleep(0.1)
    release.set()
    leader.join(3)
    follower.join(3)
    assert calls == [1] and sorted(results) == [False, True]
    assert flight.run(lambda: None) is True  # the next request is a new fetch

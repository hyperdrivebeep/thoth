"""Shared, durable admission for hosted OpenAI HTTP requests."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


class HostedReviewProviderBudgetExceeded(RuntimeError):
    pass


class SqliteHostedReviewProviderBudget:
    def __init__(self, path: Path, *, max_requests_per_day: int) -> None:
        self.path = path
        self.max_requests_per_day = max_requests_per_day

    def reserve(self, *, now: datetime | None = None) -> None:
        day = (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=30, isolation_level=None)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provider_budget "
                "(day TEXT PRIMARY KEY, used INTEGER NOT NULL)"
            )
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT used FROM provider_budget WHERE day = ?", (day,)
                ).fetchone()
                used = 0 if row is None else int(row[0])
                if used >= self.max_requests_per_day:
                    raise HostedReviewProviderBudgetExceeded("HOSTED_REVIEW_OPENAI_LIMIT")
                connection.execute(
                    "INSERT INTO provider_budget (day, used) VALUES (?, ?) "
                    "ON CONFLICT(day) DO UPDATE SET used = excluded.used",
                    (day, used + 1),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

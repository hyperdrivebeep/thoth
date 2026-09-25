"""Bounded process-local query handles hide scanned identities and row watermarks.

Restart/eviction invalidates a cursor; callers refresh rather than silently change its basis.
This contains no research truth, credentials, DB write, or durable query journal.
"""

from collections import OrderedDict
from secrets import token_urlsafe


class OpaqueHistoryCursors:
    def __init__(self, capacity: int = 4096) -> None:
        self._capacity = capacity
        self._values: OrderedDict[str, str] = OrderedDict()

    def encode(self, payload: str) -> str:
        token = token_urlsafe(32)
        self._values[token] = payload
        while len(self._values) > self._capacity:
            self._values.popitem(last=False)
        return token

    def decode(self, token: str) -> str:
        try:
            return self._values[token]
        except KeyError as exc:
            raise ValueError("HISTORY_CURSOR_EXPIRED") from exc

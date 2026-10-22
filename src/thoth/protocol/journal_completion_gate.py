"""Reentrant success-tail gates shared by LOCAL runtimes in one process."""

from _thread import RLock
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class _Entry:
    lock: RLock = field(default_factory=RLock)
    users: int = 0


_entries: dict[tuple[str, str], _Entry] = {}
_registry_lock = Lock()


@contextmanager
def journal_gate(workspace_key: str, operation_id: str) -> Generator[None]:
    """Count holders and waiters, then discard the entry after the last exit."""
    key = (workspace_key, operation_id)
    with _registry_lock:
        entry = _entries.setdefault(key, _Entry())
        entry.users += 1
    try:
        with entry.lock:
            yield
    finally:
        with _registry_lock:
            entry.users -= 1
            if entry.users == 0:
                del _entries[key]

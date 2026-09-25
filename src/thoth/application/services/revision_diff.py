from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from thoth.domain.revision import SemanticDiffEntry

_MISSING = object()


def semantic_diff(
    before: object, after: object, *, path: str = ""
) -> tuple[SemanticDiffEntry, ...]:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        before_values = cast(Mapping[object, object], before)
        after_values = cast(Mapping[object, object], after)
        before_map: dict[str, object] = {
            str(key): value for key, value in before_values.items()
        }
        after_map: dict[str, object] = {
            str(key): value for key, value in after_values.items()
        }
        changes: list[SemanticDiffEntry] = []
        for key in sorted(set(before_map) | set(after_map)):
            changes.extend(
                semantic_diff(
                    before_map.get(key, _MISSING),
                    after_map.get(key, _MISSING),
                    path=f"{path}/{_escape(key)}",
                )
            )
        return tuple(changes)
    if (
        isinstance(before, Sequence)
        and not isinstance(before, str | bytes | bytearray)
        and isinstance(after, Sequence)
        and not isinstance(after, str | bytes | bytearray)
    ):
        before_sequence = cast(Sequence[object], before)
        after_sequence = cast(Sequence[object], after)
        if list(before_sequence) == list(after_sequence):
            return ()
    elif before is not _MISSING and after is not _MISSING and before == after:
        return ()
    return (
        SemanticDiffEntry(
            path=path or "/",
            before_present=before is not _MISSING,
            after_present=after is not _MISSING,
            before=None if before is _MISSING else cast(object, before),
            after=None if after is _MISSING else cast(object, after),
        ),
    )


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")

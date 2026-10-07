"""The effect declaration of an action, read so that a flag is exactly true or false."""

from __future__ import annotations

from typing import cast

from thoth.domain.action import PROHIBITED_EFFECT_KEYS

# Effect flags are exactly true or false. Anything else (a word, a number, null) would be read as
# "not true", which can lower a forbidden effect to R0, so it is refused instead of classified.
BOOLEAN_EFFECT_KEYS = (
    *PROHIBITED_EFFECT_KEYS,
    "effect_completeness_confirmed",
    "external_write",
    "physical_action",
    "changes_official_baseline",
    "operational_equipment_change",
    "runs_untrusted_code",
    "sandbox_required",
    "changes_local_draft",
)

_UNSET = object()


def checked_effect_vector(raw: object, completeness: object = _UNSET) -> dict[str, object]:
    """The effect declaration as a dict, refused when a flag is not true or false.

    A missing key is allowed; null is not. When given, `completeness` is the declaration-level value
    used if the vector does not carry its own.
    """
    effect: dict[str, object] = (
        {str(key): child for key, child in cast(dict[object, object], raw).items()}
        if isinstance(raw, dict)
        else {}
    )
    if completeness is not _UNSET:
        effect.setdefault("effect_completeness_confirmed", completeness)
    for key in BOOLEAN_EFFECT_KEYS:
        if key in effect and not isinstance(effect[key], bool):
            raise ValueError(f"effect_vector.{key} must be true or false")
    return effect

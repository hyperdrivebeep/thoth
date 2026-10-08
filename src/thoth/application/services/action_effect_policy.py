"""Pure action effect classification and impact projection."""

from __future__ import annotations

from thoth.domain.action import PROHIBITED_EFFECT_KEYS


def classify_effect_vector(
    effect: dict[str, object],
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    # A forbidden effect is R4 whether or not the declaration is complete; completeness is only
    # asked of effects that are not forbidden.
    if any(effect.get(key) is True for key in PROHIBITED_EFFECT_KEYS):
        return (
            "R4",
            "PROHIBITED",
            ("PROHIBITED_SEMANTIC_AUTHORITY",),
            ("institution-authority",),
        )
    if effect.get("effect_completeness_confirmed") is not True:
        return (
            "R3",
            "POLICY_UNDEFINED",
            ("EFFECT_COMPLETENESS_REVIEW",),
            ("effect-owner",),
        )
    if any(
        effect.get(key) is True
        for key in (
            "external_write",
            "physical_action",
            "changes_official_baseline",
            "operational_equipment_change",
        )
    ):
        roles = ["project-owner"]
        if effect.get("physical_action") is True:
            roles.append("safety-owner")
        if effect.get("external_write") is True:
            roles.append("external-interface-owner")
        return (
            "R3",
            "APPROVAL_REQUIRED",
            ("PROTECTED_ACTION", "ACTION_TIME_PREFLIGHT"),
            tuple(roles),
        )
    if effect.get("runs_untrusted_code") is True or effect.get("sandbox_required") is True:
        return (
            "R2",
            "AUTO_ALLOWED",
            ("ISOLATED_SANDBOX",),
            ("sandbox-owner",),
        )
    if effect.get("changes_local_draft") is True:
        return (
            "R1",
            "PREAUTHORIZED",
            ("LOCAL_REVISION",),
            (),
        )
    return "R0", "AUTO_ALLOWED", ("READ_ONLY",), ()


def effect_impact(effect: dict[str, object]) -> dict[str, object]:
    return {
        "data": effect.get("data", "NONE"),
        "code": effect.get("code", "NONE"),
        "configuration": effect.get("configuration", "NONE"),
        "equipment": effect.get("equipment", "NONE"),
        "external_institution": effect.get("external_write", False),
        "baseline": effect.get("changes_official_baseline", False),
        "security": effect.get("security_consequence", "NONE"),
        "privacy": effect.get("privacy_consequence", "NONE"),
        "safety": effect.get("safety_consequence", "NONE"),
        "legal": effect.get("legal_consequence", "NONE"),
        "cost": effect.get("cost", "UNRESOLVED"),
        "time": effect.get("time", "UNRESOLVED"),
        "observability": effect.get("observability", "UNRESOLVED"),
    }

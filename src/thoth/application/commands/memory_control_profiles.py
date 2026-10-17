"""The built-in memory policy profiles and the text patterns of the candidate pre-check."""

from __future__ import annotations

import re

POLICIES = (
    {
        "profile_ref": "memory:domain-reference:1",
        "version": 1,
        "payload_mode": "DOMAIN_REFERENCE",
        "recall_classes": (
            "FACT",
            "FAILURE",
            "REFERENCE",
            "HYPOTHESIS",
            "DECISION",
            "PERSON",
            "ACTION",
        ),
        "owner": "DOMAIN_NAMESPACE",
        "promotion": "EXACT_OWNER_REVISION_REQUIRED",
        "expiry": "OWNER_OR_POLICY_BOUND",
        "retention": "AUDIT_PRESERVE",
        "team_roles": ("FACTS", "CONTRARIAN", "RISK", "GATE_REVIEWER"),
        "enabled": True,
    },
    {
        "profile_ref": "memory:assertion:1",
        "version": 1,
        "payload_mode": "MEMORY_ASSERTION",
        "recall_classes": ("LESSON", "PRACTICE"),
        "owner": "MEMORY_NAMESPACE",
        "promotion": "SOURCE_LINEAGE_AND_TEAM_GATE",
        "expiry": "TIME_AND_SUPPORT_BOUND",
        "retention": "SUPERSEDE_OR_RETIRE_NO_DELETE",
        "team_roles": ("REFLECTION", "CONTRARIAN", "RISK", "GATE_REVIEWER"),
        "enabled": True,
    },
)

INJECTION = re.compile(
    r"ignore\s+(all|previous)|system\s+prompt|developer\s+message|이전\s*명령.*무시",
    re.IGNORECASE,
)
SECRET = re.compile(r"(?:api[_-]?key|secret|password|token)\s*[:=]\s*[^\s]{8,}", re.IGNORECASE)

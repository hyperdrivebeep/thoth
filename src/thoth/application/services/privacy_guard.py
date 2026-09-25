from __future__ import annotations

import re

_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?![\w.-])")
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._~-]{8,}")
_JWT = re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{8,}\b")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|password|secret|credential)\s*[:=]\s*\S+"
)
_PHONE = re.compile(r"(?<!\d)(?:\+?82[- ]?)?0?1[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_SAFE_CODE = re.compile(r"^[0-9A-Za-z가-힣][0-9A-Za-z가-힣._:+/-]{0,159}$")


def reject_sensitive_scalar(value: object) -> None:
    if not isinstance(value, str):
        return
    if any(
        pattern.search(value) for pattern in (_EMAIL, _BEARER, _JWT, _SECRET_ASSIGNMENT, _PHONE)
    ):
        raise ValueError("identity or privacy-sensitive scalar value is prohibited")


def require_privacy_safe_code(value: object) -> None:
    reject_sensitive_scalar(value)
    if isinstance(value, str) and _SAFE_CODE.fullmatch(value) is None:
        raise ValueError("privacy-safe field value must use the bounded code grammar")

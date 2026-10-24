"""Explicit canonical codec selection, without modifying old payload defaults."""

from typing import cast


def validate_hypothesis_record_version(value: object) -> object:
    if not isinstance(value, dict):
        return value
    record = cast(dict[str, object], value)
    version = record.get("schema_version", "1.0.0")
    if version not in ("1.0.0", "1.1.0", "1.2.0", "1.3.0"):
        raise ValueError("HYPOTHESIS_RECORD_VERSION_UNSUPPORTED")
    details = record.get("generation_details")
    contract = (
        cast(dict[str, object], details).get("contract_version")
        if isinstance(details, dict)
        else getattr(details, "contract_version", None)
    )
    if (version == "1.2.0") != (contract == "hypothesis_portfolio.v4"):
        raise ValueError("HYPOTHESIS_RECORD_CODEC_MISMATCH")
    if (version == "1.3.0") != (contract == "hypothesis_portfolio.v5"):
        raise ValueError("HYPOTHESIS_RECORD_CODEC_MISMATCH")
    return record

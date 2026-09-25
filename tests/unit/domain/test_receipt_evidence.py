from datetime import UTC, datetime

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.receipt import Receipt, calculate_receipt_digest


def receipt() -> Receipt:
    return Receipt.model_validate(
        {
            "receipt_id": "receipt:legacy",
            "project_id": "project:legacy",
            "receipt_type": "TRANSITION",
            "claim_scopes": ["TRANSITION_RECORDED"],
            "subject_refs": ["project:legacy"],
            "before_head_set_digest": "a" * 64,
            "after_head_set_digest": "b" * 64,
            "policy_version": "policy:legacy",
            "integrity_state": "VALID",
            "provenance_state": "PARTIAL",
            "signature_state": "NOT_PRESENT",
            "timestamp_trust": "LOCAL_ONLY",
            "recorded_at": datetime(2026, 9, 5, tzinfo=UTC),
            "receipt_digest": "0" * 64,
        }
    )


def test_empty_evidence_preserves_legacy_digest_shape() -> None:
    item = receipt()
    legacy = item.model_dump(mode="python")
    legacy.pop("receipt_digest")
    legacy.pop("previous_transition_digest")
    legacy.pop("evidence_refs", None)
    assert calculate_receipt_digest(item) == domain_digest(
        "RECEIPT", item.schema_version, canonical_payload(legacy)
    )


def test_nonempty_evidence_is_durable_and_hash_bound() -> None:
    item = receipt()
    assert "evidence_refs" in Receipt.model_fields
    one = item.model_copy(update={"evidence_refs": ("source:one",)})
    two = item.model_copy(update={"evidence_refs": ("source:two",)})
    assert calculate_receipt_digest(one) != calculate_receipt_digest(item)
    assert calculate_receipt_digest(one) != calculate_receipt_digest(two)
    assert Receipt.model_validate(one.model_dump(mode="json")).evidence_refs == ("source:one",)

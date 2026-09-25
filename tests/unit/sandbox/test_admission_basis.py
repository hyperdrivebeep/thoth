from __future__ import annotations

from datetime import UTC, datetime

import pytest

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.sandbox import SandboxAdmissionBasis


def test_admission_basis_rejects_changed_fields_with_old_digest() -> None:
    draft: dict[str, object] = {
        "project_id": "project:admission",
        "attempt_id": "attempt:one",
        "project_revision": 1,
        "cutoff_at": datetime(2026, 9, 1, tzinfo=UTC),
        "head_set_digest": "a" * 64,
        "spec_digest": "b" * 64,
        "policy_id": "policy:one",
        "policy_revision": 1,
        "policy_digest": "c" * 64,
        "schema_version": "1.0.0",
    }
    basis = SandboxAdmissionBasis.model_validate(
        {
            **draft,
            "basis_digest": domain_digest(
                "SANDBOX_ADMISSION_BASIS", "1.0.0", canonical_payload(draft)
            ),
        }
    )
    changed = basis.model_copy(update={"project_revision": 2})
    with pytest.raises(ValueError, match="basis digest mismatch"):
        SandboxAdmissionBasis.model_validate(changed.model_dump(mode="python"))

"""New independent-output metadata preserves historical intake basis hashes."""

from datetime import UTC, datetime

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.resource_scope import (
    ResourceIntakeBasis,
    ResourceScopeTemplate,
    resource_intake_basis_digest,
)


def test_legacy_intake_basis_digest_is_unchanged_by_optional_source_owner_marker() -> None:
    legacy = {
        "project_id": "project:legacy-intake",
        "project_revision": 1,
        "policy_id": "policy:legacy",
        "policy_digest": "a" * 64,
        "template": ResourceScopeTemplate(
            owner_kind="PROJECT", visibility="PROJECT_SHARED"
        ).model_dump(mode="python"),
        "parent_digests": {},
        "actor_ref": "local:operator",
        "principal_digest": None,
        "session_ref": None,
        "role_assignment_ref": None,
        "prepared_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    original = domain_digest("RESOURCE_INTAKE_BASIS", "1.0.0", canonical_payload(legacy))
    basis = ResourceIntakeBasis.model_validate(legacy)
    assert resource_intake_basis_digest(basis) == original
    assert resource_intake_basis_digest(basis.model_copy(update={"source_owned": True})) != original

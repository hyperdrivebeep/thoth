from pathlib import Path

import pytest
from pydantic import ValidationError

from thoth.domain.resource_scope import (
    ResourceScopeBody,
    ResourceScopeRecord,
    ResourceScopeTemplate,
    seal_resource_scope,
)


@pytest.mark.parametrize("count", [127, 128, 129, 161, 1870])
def test_all_parent_references_are_preserved(count: int) -> None:
    refs = tuple(f"span:{i}" for i in range(count))
    scope = ResourceScopeTemplate(
        owner_kind="PROJECT", visibility="PROJECT_SHARED", parent_refs=refs
    )
    assert scope.parent_refs == refs
    assert ResourceScopeTemplate.model_validate_json(scope.model_dump_json()) == scope


@pytest.mark.parametrize("refs", [("",), ("x" * 261,), ("span:1", "span:1")])
def test_invalid_parent_references_remain_rejected(refs: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        ResourceScopeTemplate(owner_kind="PROJECT", visibility="PROJECT_SHARED", parent_refs=refs)


def test_pre_repair_v1_seal_roundtrips_without_hash_or_schema_change() -> None:
    raw = (
        Path(__file__).parents[1] / "fixtures/resource_scope_v1_before_parent_repair.json"
    ).read_text(encoding="utf-8")
    record = ResourceScopeRecord.model_validate_json(raw)
    assert ResourceScopeRecord.model_validate_json(record.model_dump_json()) == record
    body = ResourceScopeBody.model_validate(record.model_dump(exclude={"record_digest"}))
    assert seal_resource_scope(body).record_digest == record.record_digest
    assert "maxItems" not in ResourceScopeTemplate.model_json_schema()["properties"]["parent_refs"]
    with pytest.raises(ValidationError, match="RESOURCE_SCOPE_SELF_PARENT"):
        seal_resource_scope(body.model_copy(update={"parent_refs": (record.resource_ref,)}))

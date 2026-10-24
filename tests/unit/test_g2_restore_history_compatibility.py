"""New records retain restore families and strict history views; observation keys cannot replay."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError
from tests.fixtures.hypothesis_grounding_v5 import stored_prediction, v5_record
from tests.integration import test_state_transition_repeat_current_behavior as state_fixture

from thoth.application.services.restore_profiles import TypedRestoreProfile
from thoth.apps.restore_profiles import restore_profiles
from thoth.apps.runtime_types import AppRuntime
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.hypothesis_full import HypothesisRecord
from thoth.domain.research_history import HistoricalResultView
from thoth.domain.restore import RestoreError
from thoth.domain.revision import EntitySnapshot, SemanticRevision
from thoth.protocol.jsonrpc import JsonRpcResponse


def envelope(content: dict[str, Any]) -> tuple[SemanticRevision, EntitySnapshot]:
    snap = EntitySnapshot(
        snapshot_id="snapshot:g2",
        project_id="p",
        entity_type=EntityType.HYPOTHESIS,
        entity_id="h1",
        schema_version="1.0.0",
        content=content,
        content_digest=domain_digest("SNAPSHOT", "1.0.0", canonical_payload(content)),
    )
    revision = SemanticRevision(
        revision_id="revision:g2",
        project_id="p",
        entity_type=EntityType.HYPOTHESIS,
        entity_id="h1",
        snapshot_id=snap.snapshot_id,
        parent_revision_digests=(),
        actor=ActorRef(actor_id="actor:test", kind=ActorKind.HUMAN, role="OWNER"),
        reason="synthetic compatibility check",
        evidence_refs=(),
        affected_refs=(),
        revision_digest="d" * 64,
        created_at=datetime(2026, 10, 24, tzinfo=UTC),
    )
    return revision, snap


def test_v5_restores_through_existing_family_and_checks_row_only_evidence() -> None:
    value = v5_record()
    value["evidence_refs"] = []
    value["generation_details"]["expected_results"][0]["rows"] = [
        stored_prediction("EVIDENCE_INFERENCE")
    ]
    canonical = HypothesisRecord.model_validate(value).model_dump(mode="python")
    revision, snapshot = envelope(canonical)
    before = canonical_payload(snapshot.content)
    profile, decoded = restore_profiles().resolve(revision, snapshot)
    assert isinstance(profile, TypedRestoreProfile)
    assert isinstance(decoded, HypothesisRecord)
    assert profile.profile_id == "hypothesis.v1" and decoded.schema_version == "1.3.0"
    assert profile.evidence_refs(decoded) == ("span:0",)
    assert canonical_payload(snapshot.content) == before
    assert {"1.0.0", "1.1.0", "1.2.0", "1.3.0"} <= profile.supported_schema_versions


def test_unknown_record_version_is_still_rejected_by_restore() -> None:
    value = v5_record()
    value["schema_version"] = "1.4.0"
    with pytest.raises(RestoreError, match="RESTORE_SCHEMA_UNSUPPORTED"):
        restore_profiles().resolve(*envelope(value))


@pytest.mark.parametrize(
    "marker", ["LEGACY_DISPLAY_PROJECTION", "V4_COMPATIBILITY_PROJECTION", "V5_LEGACY_PROJECTION"]
)
def test_history_marker_has_a_closed_typed_contract(marker: str) -> None:
    # Validate the field through the real exported model without making unknown extras permissive.
    field = HistoricalResultView.model_fields["expected_result_view"]
    from pydantic import TypeAdapter

    assert TypeAdapter(field.annotation).validate_python(marker) == marker
    with pytest.raises(ValidationError):
        TypeAdapter(field.annotation).validate_python("UNSUPPORTED_PROJECTION")


@pytest.mark.asyncio
async def test_state_observer_uses_distinct_keys_even_if_object_addresses_repeat(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(state_fixture, "id", lambda _value: 42, raising=False)
    keys: list[str] = []

    class ReadBus:
        async def dispatch(self, message: Any) -> JsonRpcResponse:
            keys.append(message.params.meta.idempotency_key)
            return JsonRpcResponse(
                id=message.id,
                result={
                    "operation_id": "",
                    "state": "SUCCEEDED",
                    "value": {"execution_state": "IDLE", "revision": len(set(keys))},
                },
            )

    runtime = cast(AppRuntime, SimpleNamespace(bus=ReadBus()))
    scope: dict[str, object] = {"project_id": "p", "thread_id": "t"}
    assert await state_fixture._thread_state(runtime, scope) == ("IDLE", 1)
    assert await state_fixture._thread_state(runtime, scope) == ("IDLE", 2)
    assert len(set(keys)) == 2

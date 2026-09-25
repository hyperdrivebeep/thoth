from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from thoth.adapters.storage.history_cursors import OpaqueHistoryCursors
from thoth.application.services.history_cursor import HistoryCursor, cursor_scope, decode_cursor
from thoth.domain.auth import AuthenticatedActorContext, authenticated_actor_scope
from thoth.domain.research_basis import ResearchResultBasis
from thoth.domain.research_codec import decode_current_result_manifest
from thoth.domain.research_history import HistoryScope, HistoryTimelineInput
from thoth.domain.research_request import (
    CurrentResultManifest,
    CurrentResultManifestV21,
    RevisionRef,
)
from thoth.domain.restore import RestoreError


def ref(
    identifier: str = "request:t", digest: str = "a" * 64, entity: str = "THREAD"
) -> RevisionRef:
    return RevisionRef(
        project_id="p",
        entity_type=entity,
        entity_id=identifier,
        revision_id="revision:" + digest,
        revision_digest=digest,
    )


def basis(**values: object) -> ResearchResultBasis:
    payload: dict[str, object] = {
        "request_ref": ref(),
        "policy_digest": "f" * 64,
        "cutoff_at": datetime(2026, 9, 1, tzinfo=UTC),
        **values,
    }
    return ResearchResultBasis.model_validate(payload, strict=True)


def test_final_production_union_keeps_new_keys_and_rejects_wrong_identity():
    produced = ref("new-action", "b" * 64, "ACTION")
    captured = basis(
        consumed_heads={"HYPOTHESIS:h": "c" * 64},
        produced_refs=(produced,),
        produced_final_heads={"ACTION:new-action": "b" * 64},
    )
    assert captured.effective_expected_heads == {
        "HYPOTHESIS:h": "c" * 64,
        "ACTION:new-action": "b" * 64,
    }
    with pytest.raises(ValidationError, match="BASIS_FINAL_PRODUCER_UNRESOLVED"):
        basis(produced_refs=(produced,), produced_final_heads={"ACTION:wrong": "b" * 64})
    with pytest.raises(ValidationError, match="BASIS_FINAL_PRODUCER_UNRESOLVED"):
        basis(produced_final_heads={"ACTION:new-action": "b" * 64})


def test_v20_bytes_and_v21_explicit_codec_and_request_binding():
    old = CurrentResultManifest(
        request_ref=ref(),
        operation_id="operation:first",
        attempt_epoch=1,
        basis_digest="d" * 64,
        phase="COMPLETE",
        input_ids=(),
    )
    raw = old.model_dump(mode="json")
    assert decode_current_result_manifest(raw).model_dump(mode="json") == raw
    current = CurrentResultManifestV21.model_validate(
        {**raw, "schema_version": "2.1.0", "research_basis": basis()}
    )
    assert decode_current_result_manifest(current.model_dump()).schema_version == "2.1.0"
    with pytest.raises(ValueError, match="VERSION_UNSUPPORTED"):
        decode_current_result_manifest({**raw, "schema_version": "99.0.0"})
    with pytest.raises(ValidationError, match="RESULT_REQUEST_BASIS_MISMATCH"):
        CurrentResultManifestV21.model_validate(
            {**current.model_dump(), "request_ref": ref(digest="e" * 64)}
        )


def test_opaque_cursor_scope_caller_eviction_restart_and_repeat():
    cache = OpaqueHistoryCursors(capacity=1)
    query = HistoryTimelineInput(project_id="p", scope=HistoryScope(project_id="p"))
    cursor = HistoryCursor(
        scope_digest=cursor_scope(query),
        revisions=987,
        memories=654,
        after=("timestamp", "FULL_MEMORY", "hidden-sensitive-id"),
    )
    token = cache.encode(cursor.model_dump_json())
    with_token = query.model_copy(update={"cursor": token})
    assert "hidden" not in token and len(token) == 43
    assert decode_cursor(with_token, cache) == decode_cursor(with_token, cache) == cursor
    changed_filter = with_token.model_copy(update={"kinds": ("RESULT",)})
    with pytest.raises(RestoreError, match="HISTORY_CURSOR_INVALID"):
        decode_cursor(changed_filter, cache)
    actor = AuthenticatedActorContext(
        actor_id="human:other",
        session_id="session:other",
        project_id="p",
        role_assignment_id="role:reader",
        role="reader",
        capabilities=("READ", "REVISION"),
        data_scopes=("PROJECT",),
    )
    with (
        authenticated_actor_scope(actor),
        pytest.raises(RestoreError, match="HISTORY_CURSOR_INVALID"),
    ):
        decode_cursor(with_token, cache)
    with pytest.raises(RestoreError, match="HISTORY_CURSOR_INVALID"):
        decode_cursor(with_token, OpaqueHistoryCursors())
    cache.encode(cursor.model_dump_json())
    with pytest.raises(RestoreError, match="HISTORY_CURSOR_INVALID"):
        decode_cursor(with_token, cache)


def test_registered_source_proofs_must_agree():
    from thoth.application.services.restore_source_basis import RegisteredRestoreSources

    class Proof:
        def __init__(self, digest: str) -> None:
            self.digest = digest

        def resolve(
            self, project: str, target_digest: str, refs: tuple[str, ...]
        ) -> dict[str, tuple[str, str | None]]:
            assert refs
            return {refs[0]: ("immutable-version", self.digest)}

    with pytest.raises(RestoreError, match="RESTORE_SOURCE_DRIFT"):
        RegisteredRestoreSources((Proof("a" * 64), Proof("b" * 64))).resolve(
            "p", "target", ("span:exact",)
        )

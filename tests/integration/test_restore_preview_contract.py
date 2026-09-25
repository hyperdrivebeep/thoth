from pathlib import Path
from types import MethodType
from typing import TypedDict, cast
from uuid import uuid4

from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.storage_coverage_helpers import request
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.application.commands.restore import RestoreHandlers
from thoth.application.services.revision_service import RevisionCommitService
from thoth.apps.restore_profiles import restore_profiles
from thoth.apps.runtime_types import AppRuntime
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind
from thoth.domain.restore import RestoreError
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)
from thoth.protocol.jsonrpc import JsonRpcResponse
from thoth.protocol.registry import MethodRegistry


class StartReceipt(TypedDict):
    thread_id: str
    operation_id: str


def record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def rpc_record(response: JsonRpcResponse) -> dict[str, object]:
    assert response.error is None, response.error
    assert response.result is not None
    return record(response.result["value"])


def restore_handler(runtime: AppRuntime) -> RestoreHandlers:
    registry = vars(runtime.bus).get("_registry")
    assert isinstance(registry, MethodRegistry)
    bound = registry.resolve("revision/restore/apply")
    assert isinstance(bound, MethodType)
    owner = bound.__self__
    assert isinstance(owner, RestoreHandlers)
    return owner


async def prepared(
    tmp_path: Path,
) -> tuple[
    AppRuntime,
    ControlledResearchModel,
    StartReceipt,
    dict[str, tuple[SemanticRevision, EntitySnapshot]],
]:
    model = ControlledResearchModel(one=True)
    runtime = await setup(tmp_path, model)
    accepted = rpc_record(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "research",
                {
                    "project_id": "p",
                    "problem": "LAB-42 latency evidence",
                    "contract_version": 2,
                },
            )
        )
    )
    thread_id = accepted["thread_id"]
    operation_id = accepted["operation_id"]
    assert isinstance(thread_id, str) and isinstance(operation_id, str)
    start_receipt: StartReceipt = {"thread_id": thread_id, "operation_id": operation_id}
    await runtime.bus.drain()
    candidates: dict[str, tuple[SemanticRevision, EntitySnapshot]] = {}
    for _key, digest in runtime.ledger.read_heads("p").items():
        revision = runtime.ledger.read_revision_by_digest("p", digest)
        assert revision is not None
        snap = runtime.ledger.read_snapshot(revision.snapshot_id)
        assert snap is not None
        try:
            profile, _ = restore_profiles().resolve(revision, snap)
        except RestoreError:
            continue
        candidates.setdefault(profile.profile_id, (revision, snap))
    return runtime, model, start_receipt, candidates


def revise(
    runtime: AppRuntime,
    revision: SemanticRevision,
    snap: EntitySnapshot,
    field: str,
    new_value: object,
) -> SemanticRevision:
    unique_id = str(uuid4())
    content = {**snap.content, field: new_value}
    changed_snap = snap.model_copy(
        update={
            "snapshot_id": "snapshot:changed:" + unique_id,
            "content": content,
            "content_digest": domain_digest("SNAPSHOT", "1.0.0", canonical_payload(content)),
        }
    )
    changed = revision.model_copy(
        update={
            "revision_id": "changed:" + unique_id,
            "snapshot_id": changed_snap.snapshot_id,
            "parent_revision_digests": (revision.revision_digest,),
            "affected_refs": (),
            "revision_digest": domain_digest("TEST_REVISION", "1.0.0", canonical_payload(content)),
        }
    )
    RevisionCommitService(
        runtime.ledger, SystemClock(), UuidIdGenerator(), policy_version="test"
    ).commit(
        RevisionChangeSet(
            changeset_id="changed:" + unique_id,
            project_id="p",
            expected_heads={
                f"{revision.entity_type.value}:{revision.entity_id}": revision.revision_digest
            },
            staged_revisions=(StagedRevision(snapshot=changed_snap, revision=changed),),
            impact_plan=ImpactPropagationPlan(),
            actor=ActorRef(
                actor_id="human:local-user", kind=ActorKind.HUMAN, role="local-operator"
            ),
            reason="Independent edit",
        )
    )
    return changed


async def test_preview_profiles_are_typed_read_only_and_apply_is_closed(tmp_path: Path) -> None:
    runtime, model, _accepted, candidates = await prepared(tmp_path)
    try:
        # Exercise a closed server gate independently of the released composition default.
        restore_handler(runtime).planner.apply_ready = False
        assert len(candidates) == 6, candidates.keys()
        before = snapshot(runtime.ledger.engine)
        calls = len(model.calls)
        preview: dict[str, object] | None = None
        for profile_id, (revision, _snap) in candidates.items():
            preview = rpc_record(
                await runtime.bus.query(
                    request(
                        "revision/restore/preview",
                        profile_id,
                        {
                            "project_id": "p",
                            "contract_version": 2,
                            "selection": {
                                "project_id": "p",
                                "entity_type": revision.entity_type.value,
                                "entity_id": revision.entity_id,
                                "target_revision_digest": revision.revision_digest,
                                "expected_current_head": revision.revision_digest,
                            },
                        },
                    )
                )
            )
            assert preview["availability"] == "NO_CHANGE", preview
            assert preview["profile_id"] == profile_id
            assert record(preview["capability"])["apply_ready"] is False
            assert record(preview["capability"])["preview_supported"] is True
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
        assert len(model.calls) == calls
        assert preview is not None
        selection = record(preview["selection"])
        basis_digest = preview["basis_digest"]
        assert isinstance(basis_digest, str)
        denied_command = request(
            "revision/restore/apply",
            "readiness-closed",
            {
                "project_id": "p",
                "selection": selection,
                "preview_basis_digest": basis_digest,
                "reason": "Must remain closed",
            },
        )
        denied = await runtime.bus.dispatch(denied_command)
        assert denied.error is not None
        assert denied.error.data["reason_code"] == "RESTORE_NOT_READY"
        assert_phase_delta(
            before,
            snapshot(runtime.ledger.engine),
            failed_command_allowances(runtime.ledger.engine, denied_command),
        )
        result_key, result_digest = next(
            (key, digest)
            for key, digest in runtime.ledger.read_heads("p").items()
            if key.startswith("DECISION_OBJECT:result:")
        )
        blocked = rpc_record(
            await runtime.bus.query(
                request(
                    "revision/restore/preview",
                    "answer-read-only",
                    {
                        "project_id": "p",
                        "contract_version": 2,
                        "selection": {
                            "project_id": "p",
                            "entity_type": "DECISION_OBJECT",
                            "entity_id": result_key.split(":", 1)[1],
                            "target_revision_digest": result_digest,
                            "expected_current_head": result_digest,
                        },
                    },
                )
            )
        )
        assert blocked["reason_codes"] == ["RESTORE_SCHEMA_UNSUPPORTED"]
        assert record(blocked["capability"])["preview_supported"] is False
    finally:
        runtime.close()

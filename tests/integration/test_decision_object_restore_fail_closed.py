"""DecisionObject restore keeps its authority and transaction gates after registration."""

from hashlib import sha256
from pathlib import Path
from types import MethodType
from typing import cast

import httpx
import pytest
from pydantic import JsonValue
from tests.integration.test_a07_semantic_three_way_merge import commit, propose, request, value

from thoth.adapters.auth import SecureSessionTokenIssuer, StaticCredentialVerifier
from thoth.adapters.http.app import create_app
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import (
    SqliteAuthSessionStore,
    SqliteDependencyGraph,
    SqliteGovernanceStore,
)
from thoth.application.commands.restore import RestoreHandlers
from thoth.application.services import LocalAuthenticationService
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.relation import DependencyRelation
from thoth.protocol.jsonrpc import JsonRpcRequest
from thoth.protocol.registry import MethodRegistry

pytestmark = pytest.mark.usefixtures("xai_http_guard")


@pytest.fixture(autouse=True)
def isolated_model_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "isolated-home"
    home.mkdir()
    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "CODEX_HOME"):
        monkeypatch.setenv(name, str(home / name.lower()))
    monkeypatch.setenv("THOTH_CODEX_PACKAGE_ROOT", str(home / "missing-codex-package"))


async def changed_decision(
    tmp_path: Path, suffix: str
) -> tuple[AppRuntime, str, str, str, str, str, str]:
    workspace = tmp_path / suffix
    runtime = create_runtime(workspace)
    project_id = f"project:restore:{suffix}"
    value(
        await runtime.bus.dispatch(
            request(
                "project/create",
                f"create-{suffix}",
                {"project_id": project_id, "name": suffix, "cutoff_at": "2026-09-01T00:00:00Z"},
            )
        )
    )
    thread = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                f"thread-{suffix}",
                {
                    "project_id": project_id,
                    "thread_id": f"thread:restore:{suffix}",
                    "problem": "Restore a typed decision object",
                },
            )
        )
    )
    object_id = str(cast(list[JsonValue], thread["current_object_ids"])[0])
    key = f"DECISION_OBJECT:{object_id}"
    heads = dict(runtime.ledger.read_heads(project_id))
    base_digest = heads[key]
    base = runtime.ledger.read_revision_by_digest(project_id, base_digest)
    assert base is not None
    snapshot = runtime.ledger.read_snapshot(base.snapshot_id)
    assert snapshot is not None
    content = cast(dict[str, JsonValue], snapshot.content) | {
        "purpose_statement": "Changed for a later head"
    }
    proposal = await propose(
        runtime,
        project_id=project_id,
        object_id=object_id,
        parent=base_digest,
        content=content,
        suffix=f"{suffix}-changed",
    )
    committed = await commit(
        runtime,
        project_id=project_id,
        expected_heads=heads,
        proposal_digest=str(proposal["record_digest"]),
        suffix=f"{suffix}-changed",
    )
    current = cast(dict[str, str], committed["new_project_head_set"])[key]
    return runtime, project_id, object_id, key, base.revision_id, base_digest, current


def restore_request(
    project_id: str, object_id: str, revision_id: str, current: str, suffix: str
) -> JsonRpcRequest:
    return request(
        "revision/restore",
        f"restore-{suffix}",
        {
            "project_id": project_id,
            "entity_type": "DECISION_OBJECT",
            "entity_id": object_id,
            "selected_revision_id": revision_id,
            "expected_current_head": current,
            "reason": "Synthetic typed restore",
            "actor_id": "human:local-user",
            "actor_role": "local-operator",
        },
    )


def restore_handler(runtime: AppRuntime) -> RestoreHandlers:
    registry = vars(runtime.bus).get("_registry")
    assert isinstance(registry, MethodRegistry)
    bound = registry.resolve("revision/restore/apply")
    assert isinstance(bound, MethodType)
    owner = bound.__self__
    assert isinstance(owner, RestoreHandlers)
    return owner


async def attach_dependent(
    runtime: AppRuntime, project: str, source_key: str, bound_digest: str, suffix: str
) -> str:
    child = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                f"dependent-{suffix}",
                {
                    "project_id": project,
                    "thread_id": f"thread:dependent:{suffix}",
                    "problem": "A dependent decision with its own current head",
                },
            )
        )
    )
    dependent_id = str(cast(list[JsonValue], child["current_object_ids"])[0])
    dependent_ref = f"DECISION_OBJECT:{dependent_id}"
    SqliteDependencyGraph(runtime.ledger.engine).add(
        DependencyRelation.model_validate(
            {
                "relation_id": f"relation:restore:{suffix}",
                "project_id": project,
                "source_ref": source_key,
                "relation_type": "DERIVES",
                "target_ref": dependent_ref,
                "payload": {"reason": "synthetic currentness boundary"},
                "revision_digest": bound_digest,
            }
        )
    )
    return dependent_ref


@pytest.mark.asyncio
async def test_read_only_actor_cannot_restore_typed_decision(tmp_path: Path) -> None:
    runtime, project, object_id, key, _old_id, _base, current = await changed_decision(
        tmp_path, "permission"
    )
    before = dict(runtime.ledger.read_heads(project))
    count = len(runtime.ledger.read_revisions(project, "DECISION_OBJECT", object_id))
    try:
        actor = "local:operator"
        project_record = restore_handler(runtime).planner.projects.read(project)
        assert project_record is not None
        role = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    "decision-reader-role",
                    {
                        "project_id": project,
                        "expected_revision": project_record.revision,
                        "actor_id": actor,
                        "role": "history-reader",
                        "scope": "PROJECT",
                        "authority_tags": ["CAP_READ", "CAP_REVISION"],
                    },
                )
            )
        )["role"]
        assert isinstance(role, dict)
        auth = LocalAuthenticationService(
            sessions=SqliteAuthSessionStore(runtime.ledger.engine),
            governance=SqliteGovernanceStore(runtime.ledger.engine),
            credentials=StaticCredentialVerifier({actor: sha256(b"fixture-only").hexdigest()}),
            tokens=SecureSessionTokenIssuer(),
            clock=SystemClock(),
            ids=UuidIdGenerator(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(runtime.bus, auth=auth)),
            base_url="http://test",
        ) as client:
            issued = await client.post(
                "/auth/session",
                json={
                    "actor_id": actor,
                    "project_id": project,
                    "role_assignment_id": role["role_assignment_id"],
                },
                headers={"x-thoth-local-credential": "fixture-only"},
            )
            assert issued.status_code == 200
            headers = {"authorization": "Bearer " + issued.json()["bearer_token"]}
            selection = {
                "project_id": project,
                "entity_type": "DECISION_OBJECT",
                "entity_id": object_id,
                "target_revision_digest": _base,
                "expected_current_head": current,
            }
            preview = await client.post(
                "/rpc/query",
                json=request(
                    "revision/restore/preview",
                    "decision-reader-preview",
                    {
                        "project_id": project,
                        "contract_version": 2,
                        "selection": selection,
                    },
                ).model_dump(mode="json", by_alias=True),
                headers=headers,
            )
            assert preview.status_code == 200
            parsed = preview.json()["result"]["value"]
            assert parsed["availability"] == "AVAILABLE"
            assert parsed["capability"]["apply_ready"] is False
            assert parsed["capability"]["reason_codes"] == ["RESTORE_ACCESS_DENIED"]
            denied = await client.post(
                "/rpc",
                json=request(
                    "revision/restore/apply",
                    "decision-reader-apply",
                    {
                        "project_id": project,
                        "selection": selection,
                        "preview_basis_digest": parsed["basis_digest"],
                        "reason": "read-only actor must not restore",
                    },
                ).model_dump(mode="json", by_alias=True),
                headers=headers,
            )
            assert denied.json()["error"]["data"]["reason_code"] == "AUTH_CAPABILITY_DENIED"
        assert runtime.ledger.read_heads(project) == before
        assert runtime.ledger.read_heads(project)[key] == current
        assert len(runtime.ledger.read_revisions(project, "DECISION_OBJECT", object_id)) == count
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_historical_edge_does_not_recalculate_unrelated_current_decision(
    tmp_path: Path,
) -> None:
    runtime, project, object_id, key, old_id, base, current = await changed_decision(
        tmp_path, "stale-edge"
    )
    try:
        dependent = await attach_dependent(runtime, project, key, base, "stale-edge")
        dependent_head = runtime.ledger.read_heads(project)[dependent]
        before_state = runtime.ledger.read_dependency_states(project).get(dependent)
        restored = value(
            await runtime.bus.dispatch(
                restore_request(project, object_id, old_id, current, "stale-edge")
            )
        )
        restore = cast(dict[str, JsonValue], restored["restore"])
        assert dependent not in cast(list[str], restore["recalculate_refs"])
        assert runtime.ledger.read_heads(project)[dependent] == dependent_head
        assert runtime.ledger.read_dependency_states(project).get(dependent) == before_state
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_unavailable_trigger_evidence_cannot_be_restored(tmp_path: Path) -> None:
    runtime, project, object_id, key, _old_id, _base, current = await changed_decision(
        tmp_path, "missing-evidence"
    )
    try:
        current_revision = runtime.ledger.read_revision_by_digest(project, current)
        assert current_revision is not None
        current_snapshot = runtime.ledger.read_snapshot(current_revision.snapshot_id)
        assert current_snapshot is not None
        content = cast(
            dict[str, JsonValue],
            {**current_snapshot.content, "trigger_evidence_refs": ["span:unavailable"]},
        )
        proposal = await propose(
            runtime,
            project_id=project,
            object_id=object_id,
            parent=current,
            content=content,
            suffix="unavailable-trigger",
        )
        committed = await commit(
            runtime,
            project_id=project,
            expected_heads=dict(runtime.ledger.read_heads(project)),
            proposal_digest=str(proposal["record_digest"]),
            suffix="unavailable-trigger",
        )
        bad_head = cast(dict[str, str], committed["new_project_head_set"])[key]
        bad_revision = runtime.ledger.read_revision_by_digest(project, bad_head)
        assert bad_revision is not None
        before = dict(runtime.ledger.read_heads(project))
        count = len(runtime.ledger.read_revisions(project, "DECISION_OBJECT", object_id))
        denied = await runtime.bus.dispatch(
            restore_request(
                project,
                object_id,
                bad_revision.revision_id,
                bad_head,
                "unavailable-trigger",
            )
        )
        assert denied.error is not None
        assert denied.error.data["reason_code"] in {
            "RESTORE_IMPACT_INCOMPLETE",
            "RESTORE_SOURCE_UNAVAILABLE",
        }
        assert dict(runtime.ledger.read_heads(project)) == before
        assert len(runtime.ledger.read_revisions(project, "DECISION_OBJECT", object_id)) == count
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_fault_after_decision_restore_commit_rolls_back_head_and_impact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, project, object_id, key, old_id, _base, current = await changed_decision(
        tmp_path, "fault"
    )
    dependent = await attach_dependent(runtime, project, key, current, "fault")
    before = dict(runtime.ledger.read_heads(project))
    before_states = dict(runtime.ledger.read_dependency_states(project))
    before_count = len(runtime.ledger.read_revisions(project, "DECISION_OBJECT", object_id))
    reached = False
    host = restore_handler(runtime)
    original = host.publication.events.checkpoint

    def fail_after(*args: object, **kwargs: object) -> None:
        nonlocal reached
        original(*args, **kwargs)
        reached = True
        raise RuntimeError("SYNTHETIC_RESTORE_CHECKPOINT_FAILURE")

    monkeypatch.setattr(host.publication.events, "checkpoint", fail_after)
    try:
        failed = await runtime.bus.dispatch(
            restore_request(project, object_id, old_id, current, "fault")
        )
        assert failed.error is not None and reached
        assert dict(runtime.ledger.read_heads(project)) == before
        assert dict(runtime.ledger.read_dependency_states(project)) == before_states
        assert (
            len(runtime.ledger.read_revisions(project, "DECISION_OBJECT", object_id))
            == before_count
        )
    finally:
        runtime.close()
    monkeypatch.undo()
    reopened = create_runtime(tmp_path / "fault")
    try:
        assert reopened.ledger.read_heads(project)[key] == current
        assert reopened.ledger.read_heads(project)[dependent] == before[dependent]
        assert dict(reopened.ledger.read_dependency_states(project)) == before_states
        assert (
            len(reopened.ledger.read_revisions(project, "DECISION_OBJECT", object_id))
            == before_count
        )
    finally:
        reopened.close()

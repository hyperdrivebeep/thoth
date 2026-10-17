"""102b original public/compatibility values and effect order, including D transaction scopes."""

from __future__ import annotations

import inspect
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MethodType
from typing import Any

import pytest
from pydantic import JsonValue
from tests.integration.storage_coverage_helpers import value
from tests.integration.test_improvement_handlers_golden import Recorder, digest, plain
from tests.integration.test_m5_revision_control_atomicity import Harness, commit_body, harness
from tests.integration.test_revision_merge_late_write_observation import prepare_siblings
from tests.integration.test_revision_remaining_publication_observations import (
    prepare_baseline,
    prepare_resolve,
)

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.dependency import SqliteDependencyGraph
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.adapters.storage.transaction import SqliteAtomicUnitOfWork
from thoth.application.commands import revisions_full
from thoth.application.commands.restore_proposals import RestoreProposalHandlers
from thoth.application.commands.revisions_full import RevisionFullHandlers
from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.application.services.revision_service import RevisionCommitService
from thoth.application.services.scoped_revision_controls import ScopedRevisionControls
from thoth.application.services.semantic_merge import SemanticThreeWayMergeService
from thoth.domain.decision_object_full import DecisionObjectRecord
from thoth.protocol.notifications import notifications_for
from thoth.protocol.registry import MethodRegistry

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures/revision_publication_golden.json"
METHODS = (
    "content_read",
    "restore_preview",
    "change_set_commit",
    "merge_propose",
    "merge_resolve",
    "restore_propose",
    "baseline_decide",
    "_staged_revision",
)
SCENARIOS = (
    "generic_fast_forward",
    "generic_branch",
    "auto_merge",
    "conflict_resolve",
    "typed_approve",
    "typed_reject",
    "legacy_approve",
    "legacy_reject",
    "legacy_restore_compatibility",
)


def install(patch: pytest.MonkeyPatch, rec: Recorder) -> None:
    def now(_self: Any) -> datetime:
        rec.ticks += 1
        rec.note("clock")
        return datetime(2026, 10, 15, tzinfo=UTC) + timedelta(milliseconds=rec.ticks)

    def new(_self: Any, prefix: str) -> str:
        rec.ids += 1
        rec.note("id", prefix)
        return f"{prefix}:{rec.ids:08d}"

    patch.setattr(SystemClock, "now", now)
    patch.setattr(UuidIdGenerator, "new", new)

    def wrap(owner: Any, name: str, entry: bool = False) -> None:
        descriptor = inspect.getattr_static(owner, name)
        static = isinstance(descriptor, staticmethod)
        original = getattr(owner, name)
        label = owner.__name__ + "." + name

        def begin(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
            if entry:
                rec.depth += 1
            rec.note(
                label,
                [digest(v) for v in (args if static else args[1:])],
                {k: digest(v) for k, v in kwargs.items()},
            )

        def end(result: Any = None, error: BaseException | None = None) -> None:
            if entry:
                rec.note(
                    "return" if error is None else "raise",
                    label,
                    digest(result) if error is None else type(error).__name__,
                )
                if rec.depth == 1:
                    rec.outputs.append(
                        [
                            label,
                            plain(result)
                            if error is None
                            else {"error": type(error).__name__, "message": str(error)},
                        ]
                    )
                rec.depth -= 1

        if inspect.iscoroutinefunction(original):

            async def asynchronous(*args: Any, **kwargs: Any) -> Any:
                begin(args, kwargs)
                try:
                    result = await original(*args, **kwargs)
                except BaseException as exc:
                    end(error=exc)
                    raise
                end(result)
                return result

            traced = asynchronous
        else:

            def synchronous(*args: Any, **kwargs: Any) -> Any:
                begin(args, kwargs)
                try:
                    result = original(*args, **kwargs)
                except BaseException as exc:
                    end(error=exc)
                    raise
                end(result)
                return result

            traced = synchronous
        patch.setattr(owner, name, staticmethod(traced) if static else traced)

    for name in METHODS:
        wrap(RevisionFullHandlers, name, True)
    wrap(RestoreProposalHandlers, "commit", True)
    for owner, names in (
        (RevisionFullHandlers, ("_record", "_branch_preservation_allowed")),
        (ControlRecordService, ("create", "stage")),
        (RevisionCommitService, ("commit",)),
        (SemanticThreeWayMergeService, ("merge",)),
        (BaselineService, ("refresh", "decide", "list_candidates", "list_sets")),
        (SqliteDependencyGraph, ("downstream",)),
        (ScopedRevisionControls, ("read", "read_digest", "append")),
        (SqliteControlRecordStore, ("read", "read_digest", "list", "append")),
        (SqliteLedger, ("read_heads", "read_revision_by_digest", "read_snapshot")),
        (ResourceScopeService, ("require_read", "require_reads", "record_control_lineage")),
    ):
        for name in names:
            wrap(owner, name)

    def auth(name: str) -> None:
        original = getattr(revisions_full, name)

        def traced(*args: Any, **kwargs: Any) -> Any:
            rec.note("auth", name, [digest(v) for v in args])
            return original(*args, **kwargs)

        patch.setattr(revisions_full, name, traced)

    for name in (
        "current_authenticated_actor",
        "bind_authenticated_actor",
        "_authenticated_revision_provenance",
        "require_authenticated_authority",
    ):
        auth(name)

    def tx(owner: Any, label: str) -> None:
        original = owner.transaction

        @contextmanager
        def transaction(self: Any):
            rec.note(label, "enter")
            try:
                with original(self) as entered:
                    yield entered
            except BaseException as exc:
                rec.note(label, "rollback", type(exc).__name__)
                raise
            else:
                rec.note(label, "exit")

        patch.setattr(owner, "transaction", transaction)

    tx(SqliteLedger, "ledger")
    tx(SqliteAtomicUnitOfWork, "atomic_uow")


def root_handler(h: Harness) -> RevisionFullHandlers:
    registry = vars(h.runtime.bus)["_registry"]
    assert isinstance(registry, MethodRegistry)
    bound = registry.resolve("revision/merge/propose")
    assert isinstance(bound, MethodType) and isinstance(bound.__self__, RevisionFullHandlers)
    return bound.__self__


async def generic_commit(h: Harness, *, branch: bool) -> dict[str, Any]:
    content = {
        **h.content,
        "lifecycle": "CLOSED",
        "resolution": "DEFERRED",
        "disposition": "ARCHIVED",
    }
    DecisionObjectRecord.model_validate(content)
    proposal = value(
        await h.call(
            "revision/propose",
            "golden-proposal",
            {
                "aggregate_id": h.object_id,
                "aggregate_type": "DECISION_OBJECT",
                "parent_revision_digests": [h.heads[h.key]],
                "candidate_content": content,
                "reason": "golden status transitions",
                "evidence_refs": [],
                "actor_or_agent_ref": "agent:102-v",
                "expected_head_digest": h.heads[h.key],
                "candidate_schema_version": h.schema,
            },
        )
    )["proposal"]
    if branch:
        rival = await h.propose("rival")
        change = await h.change(rival["record_digest"], "rival-change")
        checked = await h.validate(change, "rival-validate")
        value(
            await h.call(
                "revision/changeSet/commit", "rival-commit", commit_body(h, change, checked)
            )
        )
    before = dict(h.runtime.ledger.read_heads(h.project))
    change = await h.change(proposal["record_digest"], "golden-change")
    checked = await h.validate(change, "golden-validate")
    result = value(
        await h.call("revision/changeSet/commit", "golden-commit", commit_body(h, change, checked))
    )
    assert result["change_set"]["state"] == ("BRANCHED" if branch else "COMMITTED")
    assert result["domain_transitions"] == ["object/closed", "object/dispositionChanged"]
    assert (before == dict(h.runtime.ledger.read_heads(h.project))) is branch
    return result


async def typed_baseline(h: Harness, decision: str) -> None:
    project = value(await h.call("project/read", "typed-project", {}))
    role = value(
        await h.call(
            "project/role/assign",
            "typed-role",
            {
                "expected_revision": project["revision"],
                "actor_id": "human:typed-owner",
                "role": "baseline-owner",
                "scope": "PROJECT",
                "authority_tags": ["BASELINE_OWNER"],
            },
        )
    )["role"]
    service = vars(root_handler(h))["_baselines"]
    assert isinstance(service, BaselineService)
    service.refresh(project_id=h.project, thread_id=None, purpose="golden typed baseline")
    listed = value(await h.call("revision/baseline/list", "typed-list", {}))
    candidate = listed["typed_baseline_candidates"][0]
    result = value(
        await h.call(
            "revision/baseline/decide",
            "typed-decision",
            {
                "baseline_candidate_id": candidate["candidate_id"],
                "decision": decision,
                "actor_ref": "human:typed-owner",
                "role_assignment_ref": role["role_assignment_id"],
                "approved_digest": candidate["candidate_digest"],
            },
        )
    )
    assert (result["typed_baseline_set"] is None) is (decision == "REJECT")


async def scenario(name: str, path: Path) -> None:
    async with harness(path) as h:
        if name in {"generic_fast_forward", "generic_branch"}:
            initial = h.runtime.ledger.read_revision_by_digest(h.project, h.heads[h.key])
            assert initial is not None
            snapshot = h.runtime.ledger.read_snapshot(initial.snapshot_id)
            assert snapshot is not None
            value(
                await h.call(
                    "revision/content/read",
                    "content-hit",
                    {"content_digest": snapshot.content_digest},
                )
            )
            assert (
                await h.call(
                    "revision/content/read", "content-missing", {"content_digest": "f" * 64}
                )
            ).error is not None
            await generic_commit(h, branch=name == "generic_branch")
        elif name == "auto_merge":
            body = await prepare_siblings(h)
            result = value(await h.call("revision/merge/propose", "merge", body))
            assert result["semantic_merge"]["state"] == "AUTO_MERGED"
        elif name == "conflict_resolve":
            setup = await prepare_resolve(h)
            result = value(await h.call(setup.method, "resolve", setup.body))
            assert result["merge_proposal"]["state"] == "RESOLVED"
        elif name.startswith("typed_"):
            await typed_baseline(h, "APPROVE" if name.endswith("approve") else "REJECT")
        elif name.startswith("legacy_") and name != "legacy_restore_compatibility":
            setup = await prepare_baseline(h)
            body = {**setup.body, "decision": "APPROVE" if name.endswith("approve") else "REJECT"}
            result = value(await h.call(setup.method, "legacy-decision", body))
            if name.endswith("approve"):
                assert result["baseline"]["state"] == "CURRENT"
            else:
                assert result["baseline_candidate"]["state"] == "REJECTED"
        else:
            assert name == "legacy_restore_compatibility"
            await generic_commit(h, branch=False)
            handler = root_handler(h)
            compatibility_body: dict[str, JsonValue] = {
                "project_id": h.project,
                "aggregate_id": h.object_id,
                "current_head_digest": h.runtime.ledger.read_heads(h.project)[h.key],
                "target_revision_digest": h.heads[h.key],
            }
            preview = await handler.restore_preview(compatibility_body)
            assert preview["change_set_candidate"] is True
            proposal = await handler.restore_propose(
                {
                    **compatibility_body,
                    "reason": "legacy compatibility only",
                    "evidence_refs": [],
                    "actor_or_agent_ref": "agent:102-v",
                }
            )
            assert proposal["external_effect_rollback_claimed"] is False
        assert h.model.calls == 0


@pytest.mark.parametrize("name", SCENARIOS)
async def test_revision_publication_outputs_and_effect_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    rec = Recorder()
    install(monkeypatch, rec)
    await scenario(name, tmp_path)
    assert rec.depth == 0 and rec.trace and rec.outputs
    methods = {
        "RestoreProposalHandlers.commit": "revision/changeSet/commit",
        "RevisionFullHandlers.merge_propose": "revision/merge/propose",
        "RevisionFullHandlers.merge_resolve": "revision/merge/resolve",
        "RevisionFullHandlers.baseline_decide": "revision/baseline/decide",
    }
    notices = [
        [label, notifications_for(methods[label], result)]
        for label, result in rec.outputs
        if label in methods and "error" not in result
    ]
    observed = {"trace": rec.trace, "outputs": rec.outputs, "notifications": plain(notices)}
    target = os.environ.get("THOTH_B_GOLDEN_OUT")
    if target:
        path = Path(target)
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data[name] = observed
        path.write_text(
            json.dumps(data, sort_keys=True, indent=1) + "\n", encoding="utf-8", newline="\n"
        )
    else:
        assert observed == json.loads(GOLDEN.read_text(encoding="utf-8"))[name]

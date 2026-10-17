"""102a: pure revision draft/diagnostic changes preserve public results and read/write order."""

from __future__ import annotations

import inspect
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import value
from tests.integration.test_improvement_handlers_golden import Recorder, digest, plain
from tests.integration.test_m5_revision_control_atomicity import commit_body, harness

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.adapters.storage.transaction import SqliteAtomicUnitOfWork
from thoth.application.commands import revisions_full
from thoth.application.commands.restore_proposals import RestoreProposalHandlers
from thoth.application.commands.revisions_full import RevisionFullHandlers
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.application.services.scoped_revision_controls import ScopedRevisionControls
from thoth.protocol.notifications import notifications_for

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures/revision_command_golden.json"
SCENARIOS = (
    "draft_flow",
    "head_mismatch",
    "actor_mismatch",
    "missing_dependencies",
    "held_validation",
    "branch_preserved",
    "stale_validation",
    "missing_records",
)
METHODS = (
    "propose",
    "change_set_create",
    "change_set_validate",
    "branch_create",
    "recompute_request",
    "baseline_prepare",
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
    wrap(RestoreProposalHandlers, "validate", True)
    for owner, names in (
        (RevisionFullHandlers, ("_record", "_branch_preservation_allowed")),
        (ControlRecordService, ("create", "stage")),
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


async def scenario(name: str, path: Path, patch: pytest.MonkeyPatch, rec: Recorder) -> None:
    async with harness(path) as h:
        if name in {"head_mismatch", "actor_mismatch"}:
            data: dict[str, object] = {
                "aggregate_id": h.object_id,
                "aggregate_type": "DECISION_OBJECT",
                "parent_revision_digests": [h.heads[h.key]],
                "candidate_content": h.content,
                "reason": "golden",
                "evidence_refs": [],
                "actor_or_agent_ref": "agent:102-v",
                "expected_head_digest": "0" * 64 if name == "head_mismatch" else h.heads[h.key],
            }
            if name == "actor_mismatch":

                def other_actor() -> Any:
                    rec.note("injected_actor_mismatch")
                    return SimpleNamespace(project_id=h.project, actor_id="human:different")

                patch.setattr(revisions_full, "current_authenticated_actor", other_actor)
            assert (await h.call("revision/propose", "bad-proposal", data)).error is not None
            return
        if name == "missing_dependencies":
            change = await h.change("f" * 64, "missing-change")
            assert change["state"] == "HELD"
            assert change["payload"]["missing_dependencies"] == ["f" * 64]
            return
        if name == "missing_records":
            requests: tuple[tuple[str, dict[str, object]], ...] = (
                (
                    "revision/changeSet/validate",
                    {"record_id": "missing", "expected_change_set_revision": 1},
                ),
                (
                    "revision/recompute/request",
                    {
                        "change_set_id": "missing",
                        "projection_refs": [],
                        "recompute_policy_ref": "policy",
                    },
                ),
                (
                    "revision/branch/create",
                    {
                        "aggregate_id": h.object_id,
                        "from_revision_digest": "f" * 64,
                        "purpose": "missing",
                        "actor_or_agent_ref": "agent:102-v",
                    },
                ),
                (
                    "revision/baseline/prepare",
                    {
                        "purpose": "stale",
                        "scoped_head_map": {h.key: "f" * 64},
                        "policy_version": "v1",
                        "evidence_refs": [],
                    },
                ),
            )
            for index, (method, data) in enumerate(requests):
                assert (await h.call(method, f"missing-{index}", data)).error is not None
            return
        target = await h.propose("target")
        heads = dict(h.heads)
        if name == "branch_preserved":
            rival = await h.propose("rival")
            change = await h.change(rival["record_digest"], "rival-change")
            checked = await h.validate(change, "rival-validate")
            value(
                await h.call(
                    "revision/changeSet/commit", "rival-commit", commit_body(h, change, checked)
                )
            )
        if name == "held_validation":
            heads["DECISION_OBJECT:unrelated"] = "f" * 64
        change = await h.change(target["record_digest"], "target-change", heads)
        checked = await h.validate(change, "target-validate")
        assert checked["state"] == ("HELD" if name == "held_validation" else "READY_TO_COMMIT")
        assert checked["checks"]["branch_preservation"] is (name == "branch_preserved")
        if name == "stale_validation":
            assert (
                await h.call(
                    "revision/changeSet/validate",
                    "stale-validate",
                    {
                        "record_id": change["record_id"],
                        "expected_change_set_revision": change["version"],
                    },
                )
            ).error is not None
        if name != "draft_flow":
            return
        branch = value(
            await h.call(
                "revision/branch/create",
                "branch",
                {
                    "aggregate_id": h.object_id,
                    "from_revision_digest": h.heads[h.key],
                    "purpose": "branch metadata",
                    "actor_or_agent_ref": "agent:102-v",
                },
            )
        )
        assert branch["content_mutated"] is False
        recomputed = value(
            await h.call(
                "revision/recompute/request",
                "recompute",
                {
                    "change_set_id": change["record_id"],
                    "projection_refs": ["z", "a"],
                    "recompute_policy_ref": "policy",
                },
            )
        )["recompute_task"]
        assert recomputed["payload"]["dependency_order"] == ["a", "z"]
        candidate = value(
            await h.call(
                "revision/baseline/prepare",
                "baseline",
                {
                    "purpose": "baseline candidate only",
                    "scoped_head_map": h.heads,
                    "policy_version": "v1",
                    "evidence_refs": [],
                },
            )
        )
        assert candidate["baseline_candidate"]["state"] == "PENDING"
        assert candidate["protected_authority_package"]["required_roles"] == ["baseline-owner"]
        assert h.model.calls == 0


@pytest.mark.parametrize("name", SCENARIOS)
async def test_revision_command_outputs_and_effect_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    rec = Recorder()
    install(monkeypatch, rec)
    await scenario(name, tmp_path, monkeypatch, rec)
    assert rec.depth == 0 and rec.trace and rec.outputs
    notices: list[Any] = []
    for label, response in rec.outputs:
        if label == "RestoreProposalHandlers.validate" and "error" not in response:
            notices.append(
                [response["state"], notifications_for("revision/changeSet/validate", response)]
            )
    observed = {"trace": rec.trace, "outputs": rec.outputs, "notifications": plain(notices)}
    if os.environ.get("THOTH_WRITE_GOLDEN") == "1":
        target = Path(os.environ.get("THOTH_GOLDEN_OUT", str(GOLDEN)))
        data: dict[str, Any] = (
            json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
        )
        data[name] = observed
        target.write_text(
            json.dumps(data, sort_keys=True, indent=1) + "\n", encoding="utf-8", newline="\n"
        )
    else:
        assert observed == json.loads(GOLDEN.read_text(encoding="utf-8"))[name]

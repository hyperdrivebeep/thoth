"""104-R: scope values, digest, authority checks and effects stay fixed across pure extraction."""

from __future__ import annotations

import inspect
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from tests.integration.resource_scope_helpers import ScopeHarness, denial, scope_harness, value
from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver
from tests.integration.test_improvement_handlers_golden import Recorder, digest, plain
from tests.integration.test_m5_revision_control_atomicity import NoModel
from tests.integration.test_resource_scope_transition_boundaries import assign_body, service, source

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.auth import SqliteAuthSessionStore
from thoth.adapters.storage.governance import SqliteGovernanceStore
from thoth.adapters.storage.projects import SqliteProjectStore
from thoth.adapters.storage.resource_scope import SqliteResourceScopeStore
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.adapters.storage.transaction import SqliteAtomicUnitOfWork
from thoth.application.services import resource_scope_service as scope_module
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.domain.auth import (
    AuthenticatedActorContext,
    authenticated_actor_scope,
    current_authenticated_actor,
)
from thoth.domain.resource_scope import ResourceScopePolicy, ResourceScopeTemplate
from thoth.ports.model import ModelPort

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures/resource_scope_records_golden.json"
METHODS = (
    "_policy",
    "prepare_intake",
    "stage",
    "_receipt",
    "_derived_basis",
    "_record_lineage",
    "assign_legacy",
    "_change",
    "admit",
    "ensure_derived",
    "update_scope",
    "grant",
    "revoke",
    "read_scope",
)
SCENARIOS = (
    "explicit_intake",
    "configured_intake",
    "legacy_assignment",
    "grant_revoke",
    "visibility_restrict",
    "owner_transfer",
    "pending_lineage",
    "derived_lineage",
)


class ScopeRecorder(Recorder):
    def __init__(self) -> None:
        super().__init__()
        self.writes: list[Any] = []
        self.actors: list[AuthenticatedActorContext] = []


def install(patch: pytest.MonkeyPatch, rec: ScopeRecorder) -> None:
    def now(_self: Any) -> datetime:
        rec.ticks += 1
        rec.note("clock")
        return datetime(2026, 10, 16, tzinfo=UTC) + timedelta(milliseconds=rec.ticks)

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
            if owner is ResourceScopeService and name == "ensure_derived":
                actor = current_authenticated_actor()
                if actor is not None:
                    rec.actors.append(actor)
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
                if owner is SqliteResourceScopeStore and name == "append" and rec.depth:
                    rec.writes.append([plain(args[1]), plain(args[2]), kwargs])
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
                if owner is SqliteResourceScopeStore and name == "append" and rec.depth:
                    rec.writes.append([plain(args[1]), plain(args[2]), kwargs])
                end(result)
                return result

            traced = synchronous
        patch.setattr(owner, name, staticmethod(traced) if static else traced)

    for name in METHODS:
        wrap(ResourceScopeService, name, True)
    for owner, names in (
        (
            ResourceScopeService,
            (
                "_actor",
                "_validate_basis",
                "_check_parent_basis",
                "_require_read",
                "_required_record",
                "_require_manage",
                "_check_write",
                "_principal_digest",
            ),
        ),
        (SqliteResourceScopeStore, ("read", "append")),
        (SqliteAuthSessionStore, ("read",)),
        (SqliteProjectStore, ("read",)),
        (SqliteGovernanceStore, ("read_policy", "list_roles")),
    ):
        for name in names:
            wrap(owner, name)

    def auth(name: str) -> None:
        original = getattr(scope_module, name)

        def traced(*args: Any, **kwargs: Any) -> Any:
            rec.note("auth", name, [digest(v) for v in args])
            return original(*args, **kwargs)

        patch.setattr(scope_module, name, traced)

    for name in ("current_authenticated_actor", "record_resource_use"):
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


async def scope(h: ScopeHarness, resource: str, key: str, actor: str = "alpha") -> dict[str, Any]:
    return value(await h.call(actor, "project/source/scope/read", key, {"resource_ref": resource}))[
        "scope"
    ]


async def grant(h: ScopeHarness, resource: str) -> dict[str, Any]:
    current = await scope(h, resource, "grant-basis")
    return value(
        await h.call(
            "alpha",
            "project/source/scope/grant",
            "grant",
            {
                "resource_ref": resource,
                "expected_revision": current["revision"],
                "grantee_kind": "ACTOR",
                "grantee_ref": h.actors["beta"],
                "reason": "golden grant",
            },
        )
    )["scope"]


async def scenario(name: str, path: Path, rec: ScopeRecorder) -> None:
    model = NoModel()
    policy = (
        ResourceScopePolicy(
            workstreams={
                "alpha": ResourceScopeTemplate(
                    owner_kind="WORKSTREAM", owner_workstream="alpha", visibility="WORKSTREAM"
                )
            }
        )
        if name == "configured_intake"
        else None
    )
    async with scope_harness(
        path, policy, model_resolver=StaticModelResolver(cast(ModelPort, model))
    ) as h:
        if name == "configured_intake":
            artifact = value(await h.connect("alpha", "configured", None))["artifact"][
                "artifact_id"
            ]
            assert (await scope(h, artifact, "configured-read"))["owner_workstream"] == "alpha"
        elif name == "pending_lineage":
            started = value(
                await h.call(
                    "alpha",
                    "thread/start",
                    "pending",
                    {
                        "thread_id": "thread:scope-golden",
                        "problem": "private scope golden",
                        "scope": {"workstream": "alpha"},
                    },
                )
            )
            object_id = started["current_object_ids"][0]
            value(await h.call("alpha", "object/read", "own-object", {"object_id": object_id}))
            assert (
                "error"
                in (
                    await h.call("beta", "object/read", "hidden-object", {"object_id": object_id})
                ).json()
            )
        elif name == "derived_lineage":
            refs = [await source(h, "parent-" + str(i)) for i in range(3)]
            spans = value(await h.call("alpha", "evidence/list", "spans", {}))["spans"]
            selected = [
                next(item["span_id"] for item in spans if item["artifact_id"] == ref)
                for ref in refs[:2]
            ]
            link = value(
                await h.call(
                    "alpha",
                    "evidence/link/propose",
                    "link",
                    {
                        "target_type": "DECISION_OBJECT",
                        "target_id": "case:scope-golden",
                        "relation": "QUALIFIES",
                        "span_ids": selected,
                        "observed_statement": "scoped evidence",
                        "independence_group": "golden",
                    },
                )
            )["evidence"]
            assert rec.actors
            scopes = service(h, "project/source/scope/read")
            # Public link creation above; extension is explicitly a service-port fixture.
            with authenticated_actor_scope(rec.actors[-1]), h.runtime.ledger.transaction():
                extended = scopes.ensure_derived(
                    h.project, link["evidence_id"], (refs[2],), is_new=False
                )
            assert extended.event_kind == "LINEAGE_EXTENDED" and refs[2] in extended.parent_refs
        else:
            resource = await source(h, "resource", legacy=name == "legacy_assignment")
            if name == "explicit_intake":
                assert (await scope(h, resource, "explicit-read"))["access_basis"] == "SOURCE_OWNER"
            elif name == "legacy_assignment":
                assigned = value(
                    await h.call(
                        "owner", "project/source/scope/assign", "assign", assign_body(resource)
                    )
                )["scope"]
                assert assigned["revision"] == 1
            else:
                granted = await grant(h, resource)
                assert granted["visibility"] == "EXPLICIT_GRANT"
                if name == "grant_revoke":
                    revoked = value(
                        await h.call(
                            "alpha",
                            "project/source/scope/revoke",
                            "revoke",
                            {
                                "resource_ref": resource,
                                "expected_revision": granted["revision"],
                                "grant_id": granted["grants"][0]["grant_id"],
                                "reason": "golden revoke",
                            },
                        )
                    )["scope"]
                    assert revoked["grants"][0]["state"] == "REVOKED"
                else:
                    owner = "beta" if name == "owner_transfer" else "alpha"
                    updated = value(
                        await h.call(
                            "owner" if name == "owner_transfer" else "alpha",
                            "project/source/scope/update",
                            "update",
                            {
                                "resource_ref": resource,
                                "expected_revision": granted["revision"],
                                "scope": {
                                    "owner_kind": "WORKSTREAM",
                                    "owner_workstream": owner,
                                    "visibility": "WORKSTREAM",
                                },
                                "reason": "golden update",
                            },
                        )
                    )["scope"]
                    assert updated["event_kind"] == (
                        "OWNER_CHANGED" if name == "owner_transfer" else "VISIBILITY_CHANGED"
                    )
                    assert updated["grants"][0]["state"] == "REVOKED"
                if name != "owner_transfer":
                    denial(
                        await h.call(
                            "beta",
                            "project/source/scope/read",
                            "peer-denied",
                            {"resource_ref": resource},
                        ),
                        "RESOURCE_SCOPE_MANAGEMENT_DENIED",
                    )
        assert model.calls == 0


@pytest.mark.parametrize("name", SCENARIOS)
async def test_scope_records_and_effect_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    rec = ScopeRecorder()
    install(monkeypatch, rec)
    await scenario(name, tmp_path, rec)
    assert rec.depth == 0 and rec.trace and rec.outputs and rec.writes
    observed = {"trace": rec.trace, "outputs": rec.outputs, "scope_writes": rec.writes}
    target = os.environ.get("THOTH_SCOPE_GOLDEN_OUT")
    if target:
        path = Path(target)
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data[name] = observed
        path.write_text(
            json.dumps(data, sort_keys=True, indent=1) + "\n", encoding="utf-8", newline="\n"
        )
    else:
        assert observed == json.loads(GOLDEN.read_text(encoding="utf-8"))[name]

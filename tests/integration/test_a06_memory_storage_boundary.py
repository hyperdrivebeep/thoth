"""Normal-entry RED tests: imports use only interfaces present in the frozen seed."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import Engine, func, select, update
from tests.integration.source_bound_problem import publish_fixture_problem
from tests.integration.test_a02_autonomous_acquisition import (
    DynamicA02Model,
    prepare_thread,
    request,
    value,
)

from thoth.adapters.auth import SecureSessionTokenIssuer, StaticCredentialVerifier
from thoth.adapters.http.app import create_app
from thoth.adapters.memory import (
    DeterministicRoleMemoryReviewer,
    LocalMemoryEmbedding,
    LocalRelationProjectionBuilder,
)
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteFullMemoryStore, SqliteGovernanceStore, SqliteProjectStore
from thoth.adapters.storage.auth import SqliteAuthSessionStore
from thoth.adapters.storage.schema import (
    auth_sessions,
    memory_projections,
    memory_records,
    memory_revision_ledger,
    memory_transition_receipts,
    project_roles,
    semantic_revisions,
)
from thoth.adapters.storage.transaction import _AMBIENT  # pyright: ignore[reportPrivateUsage]
from thoth.application.services import LocalAuthenticationService
from thoth.apps.runtime import create_runtime
from thoth.domain.auth import AuthenticatedActorContext, AuthSession, authenticated_actor_scope
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.governance import RoleAssignment
from thoth.domain.memory import MemoryProjection, MemoryReviewContext, MemoryRoleReview
from thoth.domain.model import ModelRequest, ModelResult
from thoth.protocol.jsonrpc import JsonRpcResponse

TModel = TypeVar("TModel", bound=BaseModel)


@pytest.mark.asyncio
async def test_normal_model_reviewer_embedding_and_graph_have_no_write_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    observations: dict[str, list[bool]] = {key: [] for key in ("model", "review", "embed", "graph")}
    original_model = DynamicA02Model.structured
    original_review = DeterministicRoleMemoryReviewer.review
    original_embed = LocalMemoryEmbedding.embed
    original_graph = LocalRelationProjectionBuilder.build

    async def model(self: DynamicA02Model, request: ModelRequest[TModel]) -> ModelResult[TModel]:
        observations["model"].append(_AMBIENT.get() is not None)
        return await original_model(self, request)

    async def review(
        self: DeterministicRoleMemoryReviewer,
        context: MemoryReviewContext,
    ) -> MemoryRoleReview:
        observations["review"].append(_AMBIENT.get() is not None)
        return await original_review(self, context)

    def embed(self: LocalMemoryEmbedding, text: str) -> tuple[int, ...]:
        observations["embed"].append(_AMBIENT.get() is not None)
        return original_embed(self, text)

    def graph(
        self: LocalRelationProjectionBuilder,
        records: tuple[tuple[str, str, str], ...],
    ) -> dict[str, tuple[str, ...]]:
        observations["graph"].append(_AMBIENT.get() is not None)
        return original_graph(self, records)

    monkeypatch.setattr(DynamicA02Model, "structured", model)
    monkeypatch.setattr(DeterministicRoleMemoryReviewer, "review", review)
    monkeypatch.setattr(LocalMemoryEmbedding, "embed", embed)
    monkeypatch.setattr(LocalRelationProjectionBuilder, "build", graph)
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a06-io-boundary",
                    {"project_id": project_id, "thread_id": f"thread:{project_id}"},
                )
            )
        )
        assert result["full_project_memory"]
        assert all(observations.values())
        assert not any(any(rows) for rows in observations.values()), observations
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["project", "cutoff", "policy"])
async def test_normal_memory_revalidates_project_and_policy_after_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    original = DeterministicRoleMemoryReviewer.review
    changed = False
    mutation_errors: list[str] = []

    async def review(
        self: DeterministicRoleMemoryReviewer,
        context: MemoryReviewContext,
    ) -> MemoryRoleReview:
        nonlocal changed
        result = await original(self, context)
        if not changed:
            if drift == "policy":
                store = SqliteGovernanceStore(runtime.ledger.engine)
                policy = store.read_policy(project_id)
                assert policy is not None
                projects = SqliteProjectStore(runtime.ledger.engine)
                project = projects.read(project_id)
                assert project is not None
                assert policy.payload.get("connector_allowlist")
                updated_payload: dict[str, object] = {**policy.payload, "connector_allowlist": []}
                updated_policy = policy.model_copy(
                    update={
                        "policy_id": policy.policy_id + ":concurrent",
                        "version": policy.version + 1,
                        "payload": updated_payload,
                        "policy_digest": domain_digest(
                            "PROJECT_POLICY",
                            "1.0.0",
                            canonical_payload(
                                {"project_id": project_id, "policy": updated_payload}
                            ),
                        ),
                    }
                )
                try:
                    with runtime.ledger.transaction():
                        store.put_policy(updated_policy)
                        assert projects.update(
                            project.model_copy(
                                update={
                                    "revision": project.revision + 1,
                                    "policy_binding_ref": updated_policy.policy_id,
                                }
                            ),
                            expected_revision=project.revision,
                        )
                except Exception as exc:
                    mutation_errors.append(type(exc).__name__ + ": " + str(exc))
                    raise
                persisted = store.read_policy(project_id)
                assert persisted is not None and persisted.policy_id == updated_policy.policy_id
            else:
                projects = SqliteProjectStore(runtime.ledger.engine)
                project = projects.read(project_id)
                assert project is not None
                changes: dict[str, object] = {"revision": project.revision + 1}
                changes["description" if drift == "project" else "cutoff_at"] = (
                    "concurrent project change"
                    if drift == "project"
                    else project.cutoff_at + timedelta(days=1)
                )
                assert projects.update(
                    project.model_copy(update=changes),
                    expected_revision=project.revision,
                )
            changed = True
        return result

    monkeypatch.setattr(DeterministicRoleMemoryReviewer, "review", review)
    try:
        response = await runtime.bus.dispatch(
            request(
                "thread/input",
                f"a06-{drift}-drift",
                {"project_id": project_id, "thread_id": f"thread:{project_id}"},
            )
        )
        assert changed, mutation_errors
        assert response.error is not None
        assert memory_counts(runtime.ledger.engine) == (0, 0, 0, 0)
        assert not any(
            key.startswith(("HYPOTHESIS:", "ACTION:", "OUTCOME:", "EVIDENCE:"))
            for key in runtime.ledger.read_heads(project_id)
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_head_change_during_review_preserves_branch_without_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    original = DeterministicRoleMemoryReviewer.review
    changed = False

    async def review(
        self: DeterministicRoleMemoryReviewer,
        context: MemoryReviewContext,
    ) -> MemoryRoleReview:
        nonlocal changed
        result = await original(self, context)
        if not changed:
            changed = True
            digest = next(iter(runtime.ledger.read_heads(project_id).values()))
            with runtime.ledger.transaction() as transaction:
                transaction.set_head(project_id, "OBJECT:concurrent-memory-review", digest)
        return result

    monkeypatch.setattr(DeterministicRoleMemoryReviewer, "review", review)
    try:
        await runtime.bus.dispatch(
            request(
                "thread/input",
                "a06-head-branch",
                {"project_id": project_id, "thread_id": f"thread:{project_id}"},
            )
        )
        heads = runtime.ledger.read_heads(project_id)
        assert "OBJECT:concurrent-memory-review" in heads
        assert not any(key.startswith(("HYPOTHESIS:", "ACTION:")) for key in heads)
        with runtime.ledger.engine.connect() as connection:
            count = connection.execute(
                select(func.count())
                .select_from(semantic_revisions)
                .where(
                    semantic_revisions.c.project_id == project_id,
                    semantic_revisions.c.entity_type == "HYPOTHESIS",
                )
            ).scalar_one()
        assert count > 0  # immutable candidates survive the complete HeadSet CAS conflict
        assert memory_counts(runtime.ledger.engine) == (0, 0, 0, 0)
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["after_revision", "after_receipt", "after_projections"])
async def test_memory_postwrite_fault_rolls_back_cycle_and_survives_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    def inject(step: str) -> None:
        if step == fault:
            raise RuntimeError("A06 injected postwrite fault")

    runtime, _connector, project_id = await prepare_thread(
        tmp_path,
        allow_connector=True,
        memory_fault_injector=inject,
    )
    if fault == "after_projections":
        original = SqliteFullMemoryStore.replace_projections

        def replace(
            self: SqliteFullMemoryStore,
            project_id: str,
            projections: tuple[MemoryProjection, ...],
        ) -> None:
            original(self, project_id, projections)
            raise RuntimeError("A06 injected postwrite fault")

        monkeypatch.setattr(SqliteFullMemoryStore, "replace_projections", replace)
    try:
        response = await runtime.bus.dispatch(
            request(
                "thread/input",
                f"a06-fault-{fault}",
                {"project_id": project_id, "thread_id": f"thread:{project_id}"},
            )
        )
        assert response.error is not None
        assert memory_counts(runtime.ledger.engine) == (0, 0, 0, 0)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path / "allowed")
    try:
        assert memory_counts(reopened.ledger.engine) == (0, 0, 0, 0)
        assert not any(
            key.startswith(("HYPOTHESIS:", "ACTION:", "OUTCOME:", "EVIDENCE:"))
            for key in reopened.ledger.read_heads(project_id)
        )
    finally:
        reopened.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift", ["revoked_session", "expired_session", "revoked_role", "role_scope"]
)
async def test_memory_rechecks_current_authenticated_session_and_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    now = datetime.now(UTC)
    await publish_fixture_problem(runtime, project_id)
    role = RoleAssignment(
        role_assignment_id="role:a06",
        project_id=project_id,
        actor_id="human:a06",
        role="RESEARCHER",
        authority_tags=("CAP_ADMIN", "CAP_READ", "CAP_THREAD", "CAP_WRITE"),
        created_at=now,
    )
    SqliteGovernanceStore(runtime.ledger.engine).create_role(role)
    session = AuthSession(
        session_id="session:a06",
        actor_id=role.actor_id,
        project_id=project_id,
        role_assignment_id=role.role_assignment_id,
        role=role.role,
        capabilities=("ADMIN", "READ", "THREAD", "WRITE"),
        data_scopes=("PROJECT",),
        token_digest="a" * 64,
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    SqliteAuthSessionStore(runtime.ledger.engine).put(session)
    actor = AuthenticatedActorContext(
        actor_id=session.actor_id,
        session_id=session.session_id,
        project_id=project_id,
        role_assignment_id=role.role_assignment_id,
        role=role.role,
        capabilities=session.capabilities,
        data_scopes=session.data_scopes,
    )
    original = DeterministicRoleMemoryReviewer.review
    changed = False

    async def review(
        self: DeterministicRoleMemoryReviewer,
        context: MemoryReviewContext,
    ) -> MemoryRoleReview:
        nonlocal changed
        result = await original(self, context)
        if not changed:
            changed = True
            if drift == "revoked_role":
                SqliteGovernanceStore(runtime.ledger.engine).revoke_role(
                    project_id,
                    role.role_assignment_id,
                    revoked_at=datetime.now(UTC).isoformat(),
                )
            elif drift == "role_scope":
                with runtime.ledger.engine.begin() as connection:
                    connection.execute(
                        update(project_roles)
                        .where(
                            project_roles.c.role_assignment_id == role.role_assignment_id,
                        )
                        .values(scope="WORKSTREAM:changed")
                    )
                current_role = next(
                    item
                    for item in SqliteGovernanceStore(
                        runtime.ledger.engine,
                    ).list_roles(project_id)
                    if item.role_assignment_id == role.role_assignment_id
                )
                assert current_role.scope == "WORKSTREAM:changed"
            else:
                edited = session.model_copy(
                    update=(
                        {"state": "REVOKED", "revoked_at": datetime.now(UTC)}
                        if drift == "revoked_session"
                        else {"expires_at": now - timedelta(seconds=1)}
                    )
                )
                with runtime.ledger.engine.begin() as connection:
                    connection.execute(
                        update(auth_sessions)
                        .where(
                            auth_sessions.c.session_id == session.session_id,
                        )
                        .values(
                            content_json=edited.model_dump_json(),
                            state=edited.state,
                            expires_at=edited.expires_at.isoformat(),
                        )
                    )
        return result

    monkeypatch.setattr(DeterministicRoleMemoryReviewer, "review", review)
    try:
        with authenticated_actor_scope(actor):
            response = await runtime.bus.dispatch(
                request(
                    "thread/input",
                    f"a06-auth-{drift}",
                    {"project_id": project_id, "thread_id": f"thread:{project_id}"},
                )
            )
        assert changed, response.error
        assert response.error is not None
        assert memory_counts(runtime.ledger.engine) == (0, 0, 0, 0)
    finally:
        runtime.close()


@pytest.mark.parametrize("expire_session", [False, True])
async def test_real_http_session_stays_current_through_memory_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expire_session: bool,
) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    original = DeterministicRoleMemoryReviewer.review
    reviewed = False
    try:
        await publish_fixture_problem(runtime, project_id)
        project = value(
            await runtime.bus.dispatch(
                request(
                    "project/read",
                    "http-project",
                    {"project_id": project_id},
                )
            )
        )
        role = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    "http-role",
                    {
                        "project_id": project_id,
                        "expected_revision": project["revision"],
                        "actor_id": "human:memory-owner",
                        "role": "project-owner",
                        "scope": "PROJECT",
                        "authority_tags": [
                            "CAP_ADMIN",
                            "CAP_READ",
                            "CAP_WRITE",
                            "CAP_THREAD",
                            "CAP_REVISION",
                        ],
                    },
                )
            )
        )["role"]
        assert isinstance(role, dict)
        role_assignment_id = role["role_assignment_id"]
        assert isinstance(role_assignment_id, str)
        auth = LocalAuthenticationService(
            sessions=SqliteAuthSessionStore(runtime.ledger.engine),
            governance=SqliteGovernanceStore(runtime.ledger.engine),
            credentials=StaticCredentialVerifier(
                {
                    "human:memory-owner": hashlib.sha256(b"memory-fixture").hexdigest(),
                }
            ),
            tokens=SecureSessionTokenIssuer(),
            clock=SystemClock(),
            ids=UuidIdGenerator(),
            lifetime=timedelta(seconds=30),
        )
        issued = await auth.issue_http_session(
            actor_id="human:memory-owner",
            credential="memory-fixture",
            project_id=project_id,
            role_assignment_id=role_assignment_id,
        )

        async def review(
            self: DeterministicRoleMemoryReviewer,
            context: MemoryReviewContext,
        ) -> MemoryRoleReview:
            nonlocal reviewed
            result = await original(self, context)
            reviewed = True
            if expire_session:

                def expired_now(clock: SystemClock) -> datetime:
                    del clock
                    return issued.session.expires_at + timedelta(seconds=1)

                monkeypatch.setattr(SystemClock, "now", expired_now)
            return result

        monkeypatch.setattr(DeterministicRoleMemoryReviewer, "review", review)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(runtime.bus, auth=auth)),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/rpc",
                json=request(
                    "thread/input",
                    "http-memory",
                    {
                        "project_id": project_id,
                        "thread_id": f"thread:{project_id}",
                    },
                ).model_dump(mode="json", by_alias=True),
                headers={
                    "Authorization": "Bearer " + issued.bearer_token,
                },
            )
        assert response.status_code == 200
        result = JsonRpcResponse.model_validate(response.json())
        assert reviewed
        if expire_session:
            assert result.error is not None
            assert memory_counts(runtime.ledger.engine) == (0, 0, 0, 0)
        else:
            assert result.error is None, result.error
            assert value(result)["full_project_memory"]
            assert memory_counts(runtime.ledger.engine)[1] > 0
    finally:
        runtime.close()


def memory_counts(engine: Engine) -> tuple[int, ...]:
    with engine.connect() as connection:
        return tuple(
            int(connection.execute(select(func.count()).select_from(table)).scalar_one())
            for table in (
                memory_records,
                memory_revision_ledger,
                memory_transition_receipts,
                memory_projections,
            )
        )

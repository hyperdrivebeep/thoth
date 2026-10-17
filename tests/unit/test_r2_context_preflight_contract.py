"""Observe the existing root's R2 pre-I/O decisions, queries and execution boundary."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from thoth.application.commands import thread_analysis
from thoth.application.commands.thread_analysis import ThreadAnalysisCommandHandlers
from thoth.application.services.r2_closed_loop import R2ClosedLoopCoordinator, R2ClosedLoopExecution
from thoth.domain.action import ActionCandidate, ActionPlan
from thoth.domain.auth import AuthenticatedActorContext
from thoth.domain.enums import ProjectLifecycle, RiskTier
from thoth.domain.governance import RoleAssignment
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.project import Project, WorkThread
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.project import ProjectStorePort

T0 = datetime(2026, 9, 1, microsecond=123100, tzinfo=UTC)


def project() -> Project:
    return Project(
        project_id="p",
        name="fixture",
        cutoff_at=T0,
        overlay="fixture",
        policy_binding_ref="policy:p",
    )


def actor() -> AuthenticatedActorContext:
    return AuthenticatedActorContext(
        actor_id="actor",
        session_id="session",
        project_id="p",
        role_assignment_id="role",
        role="sandbox-owner",
        capabilities=(),
        data_scopes=("allowed",),
    )


def role(**updates: object) -> RoleAssignment:
    return RoleAssignment.model_validate(
        {
            "role_assignment_id": "role",
            "project_id": "p",
            "actor_id": "actor",
            "role": "sandbox-owner",
            "scope": "allowed",
            "created_at": T0,
            **updates,
        }
    )


class Harness(ThreadAnalysisCommandHandlers):
    def __init__(self, patch: pytest.MonkeyPatch) -> None:
        self.events: list[str] = []
        self.original_project = project()
        self.current: Project | None = self.original_project
        self.authenticated: AuthenticatedActorContext | None = None
        self.roles: tuple[RoleAssignment, ...] = ()
        self.fault_at: str | None = None
        self.fault: BaseException = RuntimeError("controlled boundary failure")
        self.executed: list[Project] = []
        self.reply = R2ClosedLoopExecution(projection={"stub": "returned"})

        def event(name: str) -> None:
            self.events.append(name)
            if name == self.fault_at:
                raise self.fault

        def read(identifier: str) -> Project | None:
            assert identifier == "p"
            event("project")
            return self.current

        def authenticated() -> AuthenticatedActorContext | None:
            event("actor")
            return self.authenticated

        def roles(identifier: str) -> tuple[RoleAssignment, ...]:
            assert identifier == "p"
            event("roles")
            return self.roles

        async def execute(
            *,
            project: Project,
            thread: WorkThread,
            portfolio: HypothesisPortfolio,
            action_plan: ActionPlan,
        ) -> R2ClosedLoopExecution:
            event("execute")
            self.executed.append(project)
            return self.reply

        projects = Mock(spec=ProjectStorePort)
        projects.read.side_effect = read
        governance = Mock(spec=GovernanceStorePort)
        governance.list_roles.side_effect = roles
        coordinator = Mock(spec=R2ClosedLoopCoordinator)
        coordinator.execute_if_required = AsyncMock(side_effect=execute)
        self._projects = cast(ProjectStorePort, projects)
        self._governance = cast(GovernanceStorePort, governance)
        self._r2_closed_loop = cast(R2ClosedLoopCoordinator, coordinator)
        patch.setattr(thread_analysis, "current_authenticated_actor", authenticated)

    async def run(self, selection: str = "r2") -> R2ClosedLoopExecution | None:
        # Partial fixtures supply what preflight reads; normal RPCs cover full models.
        alternatives = (
            ()
            if selection == "empty"
            else (
                ActionCandidate.model_construct(
                    action_id="a", risk_tier=RiskTier.R0 if selection == "r0" else RiskTier.R2
                ),
            )
        )
        plan = ActionPlan.model_construct(
            alternatives=alternatives, frontier=() if selection == "outside" else ("a",)
        )
        return await self._execute_r2(
            project=self.original_project,
            thread=WorkThread(
                thread_id="t",
                project_id="p",
                cycle_id="c",
                problem="fixture",
                working_head_digest="a" * 64,
            ),
            portfolio=HypothesisPortfolio.model_construct(),
            action_plan=plan,
        )


@pytest.mark.parametrize("selection", ["empty", "r0", "outside"])
async def test_no_r2_frontier_performs_no_queries(
    selection: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(monkeypatch)
    assert await h.run(selection) is None
    assert h.events == [] and h.executed == []


@pytest.mark.parametrize(
    "change", ["missing", "cutoff", "policy", "overlay", "closing", "archived"]
)
async def test_project_hold_precedes_actor_and_role_lookup(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(monkeypatch)
    changes = {
        "cutoff": {"cutoff_at": T0 + timedelta(milliseconds=2)},
        "policy": {"policy_binding_ref": "policy:other"},
        "overlay": {"overlay": "other"},
        "closing": {"lifecycle": ProjectLifecycle.CLOSING},
        "archived": {"lifecycle": ProjectLifecycle.ARCHIVED_READ_ONLY},
    }
    h.current = (
        None if change == "missing" else h.original_project.model_copy(update=changes[change])
    )
    h.authenticated = actor()  # Conflicting authority would fail too, but must never be read.
    result = await h.run()
    assert result is not None
    assert list(result.projection.items()) == [
        ("terminal_state", "HOLD"),
        ("reason_code", "R2_PROJECT_CONTEXT_CHANGED"),
        ("scientific_truth_state", "NOT_CERTIFIED"),
        ("pre_io", True),
    ]
    assert h.events == ["project"] and h.executed == []


@pytest.mark.parametrize("change", ["same", "precision", "fresh_object"])
async def test_no_actor_skips_roles_and_executes_current_identity(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(monkeypatch)
    if change == "precision":
        h.current = h.original_project.model_copy(
            update={"cutoff_at": T0 + timedelta(microseconds=1)}
        )
    elif change == "fresh_object":
        h.current = h.original_project.model_copy(update={"name": "fresh current name"})
    assert await h.run() is h.reply
    assert h.events == ["project", "actor", "execute"]
    assert len(h.executed) == 1 and h.executed[0] is h.current


@pytest.mark.parametrize(
    "case", ["empty", "missing", "inactive", "actor", "role", "scope", "duplicate", "valid"]
)
async def test_authority_decision_and_first_matching_role(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(monkeypatch)
    h.authenticated = actor()
    changes = {
        "missing": {"role_assignment_id": "other"},
        "inactive": {"state": "REVOKED", "revoked_at": T0},
        "actor": {"actor_id": "other"},
        "role": {"role": "other"},
        "scope": {"scope": "other"},
    }
    h.roles = () if case == "empty" else (role(**changes.get(case, {})),)
    if case == "duplicate":
        h.roles = (role(state="REVOKED", revoked_at=T0), role())
    result = await h.run()
    if case == "valid":
        assert result is h.reply and len(h.executed) == 1 and h.executed[0] is h.current
        assert h.events == ["project", "actor", "roles", "execute"]
    else:
        assert result is not None
        assert list(result.projection.items()) == [
            ("terminal_state", "AUTHORITY_REQUIRED"),
            ("reason_code", "R2_AUTHORITY_CHANGED"),
            ("scientific_truth_state", "NOT_CERTIFIED"),
            ("pre_io", True),
        ]
        assert h.events == ["project", "actor", "roles"] and h.executed == []


@pytest.mark.parametrize("stage", ["project", "actor", "roles", "execute"])
async def test_failures_propagate_same_object_without_later_calls(
    stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(monkeypatch)
    h.authenticated, h.roles, h.fault_at = actor(), (role(),), stage
    with pytest.raises(RuntimeError) as caught:
        await h.run()
    assert caught.value is h.fault
    order = ["project", "actor", "roles", "execute"]
    assert h.events == order[: order.index(stage) + 1] and h.executed == []


async def test_execution_cancellation_is_not_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(monkeypatch)
    h.fault_at, h.fault = "execute", asyncio.CancelledError("controlled cancellation")
    with pytest.raises(asyncio.CancelledError) as caught:
        await h.run()
    assert caught.value is h.fault
    assert h.events == ["project", "actor", "execute"] and h.executed == []

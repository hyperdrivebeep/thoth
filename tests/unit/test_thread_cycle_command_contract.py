"""Capture the real root's command before any model execution."""

from dataclasses import fields
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from thoth.application.commands import thread_analysis
from thoth.application.commands.thread_analysis import (
    ThreadAnalysisCommandHandlers,
    ThreadInputRequest,
)
from thoth.application.workflows import ThreadCycleCommand, ThreadCycleService
from thoth.application.workflows.thread_cycle import ThreadCycleResult
from thoth.domain.auth import AuthenticatedActorContext
from thoth.domain.criterion import CriterionCandidate
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.project import Project, WorkThread
from thoth.ports.model import ModelPort


class Capture(ThreadAnalysisCommandHandlers):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.commands: list[ThreadCycleCommand] = []
        self.factory_inputs: list[tuple[ModelPort, Project]] = []
        self.stop = RuntimeError("stop before cycle execution")

    def _create_cycle(self, model: ModelPort, project: Project) -> ThreadCycleService:
        self.events.append("cycle")
        self.factory_inputs.append((model, project))
        cycle = Mock(spec=ThreadCycleService)

        async def execute(command: ThreadCycleCommand) -> ThreadCycleResult:
            self.events.append("execute")
            self.commands.append(command)
            raise self.stop

        cycle.execute = AsyncMock(side_effect=execute)
        return cast(ThreadCycleService, cycle)

    async def capture(
        self,
        request: ThreadInputRequest,
        project: Project,
        thread: WorkThread,
        criteria: tuple[CriterionCandidate, ...],
        evidence: tuple[EvidenceSpan, ...],
        model: ModelPort,
    ) -> None:
        await self._execute_cycle(
            request=request,
            project=project,
            thread=thread,
            model=model,
            work=None,
            analysis_problem="expanded analysis question",
            criteria=criteria,
            selected_evidence=evidence,
            pending_inputs=(),
        )


def inputs(
    object_ids: tuple[str, ...] = ("object:z", "object:a"),
) -> tuple[
    ThreadInputRequest,
    Project,
    WorkThread,
    tuple[CriterionCandidate, ...],
    tuple[EvidenceSpan, ...],
]:
    return (
        ThreadInputRequest(
            project_id="p",
            thread_id="t",
            provider="fixture",
            model="model",
            expected_head_overrides={"DECISION_OBJECT:object:z": "a" * 64},
        ),
        Project(
            project_id="p",
            name="fixture",
            cutoff_at=datetime(2026, 9, 1, tzinfo=UTC),
            overlay="fixture",
            policy_binding_ref="policy:p",
        ),
        WorkThread(
            thread_id="t",
            project_id="p",
            cycle_id="c",
            problem="original question",
            working_head_digest="a" * 64,
            current_object_ids=object_ids,
            scope={"site": "scoped"},
        ),
        (CriterionCandidate.model_construct(criterion_id="criterion:1"),),
        (EvidenceSpan.model_construct(span_id="span:1"),),
    )


@pytest.mark.parametrize("authenticated", [False, True])
async def test_command_fields_policy_identity_and_actor_lookup_order(
    authenticated: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Capture()
    args = inputs()
    request, project, thread, criteria, evidence = args
    model = cast(ModelPort, Mock(spec=ModelPort))
    initiator = (
        AuthenticatedActorContext(
            actor_id="user:1",
            session_id="session:42",
            project_id="p",
            role_assignment_id="role:42",
            role="project-owner",
            capabilities=(),
            data_scopes=("project:p",),
        )
        if authenticated
        else None
    )

    def actor() -> AuthenticatedActorContext | None:
        h.events.append("actor")
        return initiator

    monkeypatch.setattr(thread_analysis, "current_authenticated_actor", actor)
    with pytest.raises(RuntimeError) as caught:
        await h.capture(*args, model)
    assert caught.value is h.stop
    assert h.events == ["actor", "cycle", "execute"]
    assert h.factory_inputs[0][0] is model and h.factory_inputs[0][1] is project
    assert len(h.commands) == 1
    command = h.commands[0]
    actual = {field.name: getattr(command, field.name) for field in fields(command)}
    actual["sufficiency_signals"] = command.sufficiency_signals.model_dump(mode="json")
    actual["action_policy"] = command.action_policy.model_dump(mode="json")
    actual["actor"] = command.actor.model_dump(mode="json")
    assert actual == {
        "case_id": "case:t",
        "project_id": "p",
        "thread_id": "t",
        "object_id": "object:z",
        "problem": "expanded analysis question",
        "cutoff_at": project.cutoff_at,
        "criteria": criteria,
        "evidence": evidence,
        "sufficiency_signals": {
            "comparison_context_confirmed": None,
            "counterevidence_checked": False,
            "instrumentation_verified": None,
            "expert_semantics_confirmed": False,
        },
        "action_policy": {
            "minimum_tier_by_family": {
                "EVIDENCE_REQUEST": "R0",
                "READ_ONLY_ANALYSIS": "R0",
                "SANDBOX_REPLAY": "R2",
                "CONFIGURATION_CHANGE": "R3",
                "CONTROLLED_RERUN": "R3",
                "OFFICIAL_CRITERION_CHANGE": "R4",
            },
            "approver_role_by_family": {
                "EVIDENCE_REQUEST": "project-owner",
                "READ_ONLY_ANALYSIS": "project-owner",
                "SANDBOX_REPLAY": "sandbox-owner",
                "CONFIGURATION_CHANGE": "configuration-owner",
                "CONTROLLED_RERUN": "test-owner",
                "OFFICIAL_CRITERION_CHANGE": "criterion-owner",
            },
            "unknown_family_tier": "R3",
        },
        "actor": {
            "actor_id": "agent:fixture",
            "kind": "AGENT",
            "role": "thread-cycle-runner",
            "model_id": None,
            "tool_version": None,
            "project_id": "p",
            "session_id": "session:42" if authenticated else None,
            "role_assignment_ref": "role:42" if authenticated else None,
        },
        "policy_version": "policy:p",
        "model_policy_ref": "model-policy:fixture-v1",
        "expected_head_overrides": request.expected_head_overrides,
        "memory_scope": thread.scope,
    }
    assert command.criteria is criteria and command.evidence is evidence
    assert command.expected_head_overrides is request.expected_head_overrides
    assert command.memory_scope is thread.scope
    assert cast(Mock, model).mock_calls == []


async def test_empty_object_ids_still_raise_index_error_before_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Capture()
    monkeypatch.setattr(thread_analysis, "current_authenticated_actor", lambda: None)
    with pytest.raises(IndexError):
        await h.capture(*inputs(()), cast(ModelPort, Mock(spec=ModelPort)))
    assert h.events == ["cycle"] and h.commands == []

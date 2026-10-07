"""The places that compare a stored cutoff with the project's cutoff take the stored precision.

A project can hold a cutoff typed with sub-millisecond digits; everything stored from it keeps
milliseconds. Each test below fails on a plain datetime comparison and passes when the two are
compared at the stored precision, and each still refuses a cutoff that moved by a millisecond."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from thoth.application.commands.thread_analysis import ThreadAnalysisCommandHandlers
from thoth.application.services.hypothesis_test_lifecycle import HypothesisTestLifecycle
from thoth.application.services.memory_admission import MemoryAdmissionService
from thoth.domain.enums import RiskTier
from thoth.domain.governance import ProjectPolicy
from thoth.domain.project import Project

TYPED = datetime(2026, 8, 31, 0, 0, 0, 456, tzinfo=UTC)  # the project, typed to the microsecond
STORED = datetime(2026, 8, 31, 0, 0, 0, 0, tzinfo=UTC)  # what a stored record keeps
MOVED = STORED + timedelta(milliseconds=1)


def project(cutoff: datetime) -> Project:
    return Project(
        project_id="p",
        name="demo",
        cutoff_at=cutoff,
        overlay="default",
        policy_binding_ref="policy:x",
    )


class Projects:
    def __init__(self, cutoff: datetime) -> None:
        self.cutoff = cutoff

    def read(self, project_id: str) -> Project:
        return project(self.cutoff)


class Governance:
    def read_policy(self, project_id: str) -> ProjectPolicy:
        return ProjectPolicy(
            policy_id="policy:x",
            project_id=project_id,
            version=1,
            payload={},
            policy_digest="a" * 64,
            created_at=STORED,
        )


class Clock:
    def now(self) -> datetime:
        return datetime(2030, 1, 1, tzinfo=UTC)


def memory_service() -> MemoryAdmissionService:
    return MemoryAdmissionService(
        projects=cast(Any, Projects(TYPED)),
        governance=cast(Any, Governance()),
        sessions=cast(Any, object()),
        clock=cast(Any, Clock()),
    )


def capture(cutoff: datetime) -> None:
    memory_service().capture(project_id="p", cutoff_at=cutoff, scope={}, actor=None)


def test_memory_admission_takes_the_stored_cutoff_and_still_refuses_a_moved_one() -> None:
    # Past the cutoff check, the next refusal is the empty policy digest.
    with pytest.raises(ValueError, match="MEMORY_POLICY_DIGEST_MISMATCH"):
        capture(STORED)
    with pytest.raises(ValueError, match="MEMORY_PROJECT_POLICY_UNAVAILABLE"):
        capture(MOVED)


def prediction(knowledge_cutoff: datetime) -> None:
    lifecycle = object.__new__(HypothesisTestLifecycle)
    lifecycle.__dict__.update(_projects=Projects(TYPED), _clock=Clock())
    current = cast(Any, SimpleNamespace(project_id="p", primary_intent=None))
    lifecycle.prepare_prediction(
        current,
        knowledge_cutoff=knowledge_cutoff,
        measurement_contract_ref="contract",
        conditions={},
        expected_outcome={},
    )


def test_a_prediction_cutoff_equal_to_the_project_cutoff_at_stored_precision_is_accepted() -> None:
    with pytest.raises(ValueError, match="PREDICTION_INTENT_REQUIRED"):  # the cutoff check passed
        prediction(STORED)
    with pytest.raises(ValueError, match="PREDICTION_CUTOFF_BASIS_UNRESOLVED"):
        prediction(STORED - timedelta(milliseconds=2))


async def r2_hold_reason(stored_project_cutoff: datetime) -> str | None:
    handlers = object.__new__(ThreadAnalysisCommandHandlers)
    handlers.__dict__.update(_projects=Projects(TYPED))
    plan = cast(
        Any,
        SimpleNamespace(
            alternatives=(SimpleNamespace(risk_tier=RiskTier.R2, action_id="a"),), frontier=("a",)
        ),
    )
    try:
        held = await handlers._execute_r2(  # pyright: ignore[reportPrivateUsage]
            project=project(stored_project_cutoff),
            thread=cast(Any, None),
            portfolio=cast(Any, None),
            action_plan=plan,
        )
    except AttributeError:
        return None  # went on past the project check into work this test does not set up
    return None if held is None else str(held.projection.get("reason_code"))


@pytest.mark.asyncio
async def test_the_closed_loop_run_ignores_a_cutoff_difference_below_a_millisecond() -> None:
    assert await r2_hold_reason(STORED) != "R2_PROJECT_CONTEXT_CHANGED"
    assert await r2_hold_reason(MOVED) == "R2_PROJECT_CONTEXT_CHANGED"


CUTOFF_COMPARISON = re.compile(
    r"(\.cutoff_at|knowledge_cutoff|captured_cutoff_at|\bcutoff)\s*(!=|==)"
    r"|(!=|==)\s*[\w.]*(\.cutoff_at|knowledge_cutoff|captured_cutoff_at|\bcutoff_at)\b"
)


def test_no_cutoff_is_compared_as_a_plain_datetime_anywhere_in_the_program() -> None:
    """A new comparison of two cutoffs must go through same_stored_instant.

    Text forms (isoformat strings) and words such as "ELIGIBLE" are not datetimes and stay allowed.
    """
    offenders: list[str] = []
    for path in sorted((Path(__file__).parents[2] / "src" / "thoth").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if CUTOFF_COMPARISON.search(line) and "isoformat()" not in line and '"' not in line:
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == []

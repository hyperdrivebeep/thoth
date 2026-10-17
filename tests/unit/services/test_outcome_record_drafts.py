"""Pure Outcome record construction after the 95 move: values, id and clock order, immutability."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from thoth.application.services.outcome_record_drafts import (
    BUILTIN_OUTCOME_PROFILES,
    assessed_series_updates,
    assessment_record,
    attribution_record,
    attribution_support,
    change_set_record,
    impact_record,
    judge_assessment,
    linked_phase_updates,
    missing_series_fields,
    outcome_profile_record,
    reassessed_record,
    record_digest,
    series_record,
)
from thoth.application.services.outcome_revision_drafts import (
    commit_parts,
    revised_assessment_record,
    revised_series_record,
)
from thoth.domain.canonical import canonical_payload, domain_digest

BASE = datetime(2026, 10, 15, tzinfo=UTC)
PLAN = "a" * 64
BASELINE = "b" * 64


class Ports:
    """One log for ids and clock so their relative order is visible."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self.counter = 0
        self.ticks = 0

    def new(self, prefix: str) -> str:
        self.log.append("ids:" + prefix)
        self.counter += 1
        return f"{prefix}:{self.counter}"

    def now(self) -> datetime:
        self.log.append("clock")
        self.ticks += 1
        return BASE + timedelta(seconds=self.ticks)


def ports_args(ports: Ports) -> dict[str, Any]:
    return {"ids": cast(Any, ports), "clock": cast(Any, ports)}


def profile() -> Any:
    return outcome_profile_record(*BUILTIN_OUTCOME_PROFILES[1])


def series(ports: Ports | None = None, phases: tuple[str, ...] = ("INTERIM", "FOLLOW_UP")) -> Any:
    return series_record(
        "project:u",
        "object:u",
        PLAN,
        None,
        "EXPERIMENT_LEARNING_OUTCOME",
        BASELINE,
        tuple({"assessment_phase": phase} for phase in phases),
        phases,
        **ports_args(ports or Ports()),
    )


def assessment(
    current: Any, validity: str = "LIMITED", limitations: list[str] | None = None, **fields: Any
) -> Any:
    values: dict[str, Any] = {
        "profile": profile(),
        "phase": "INTERIM",
        "observation_refs": ("span:1",),
        "comparator_refs": ("span:2",),
        "assumptions": ("assume",),
        "validity": validity,
        "limitations": limitations or ["note"],
        "attribution": "ASSOCIATED",
    }
    values.update(fields)
    return assessment_record(current, **values, **ports_args(Ports()))


def test_builtin_profiles_keep_their_catalog() -> None:
    assert [item[0] for item in BUILTIN_OUTCOME_PROFILES] == [
        "REQUIREMENT_VERIFICATION_OUTCOME",
        "EXPERIMENT_LEARNING_OUTCOME",
        "DELIVERY_ACKNOWLEDGEMENT_OUTCOME",
    ]
    assert BUILTIN_OUTCOME_PROFILES[2] == (
        "DELIVERY_ACKNOWLEDGEMENT_OUTCOME",
        "Delivery acknowledgement outcome",
        ("COMMUNICATION_SUBMISSION",),
        ("delivery_receipt",),
        ("delivery_acknowledgement",),
        "NOT_APPLICABLE",
    )


def test_record_digest_is_the_domain_digest_of_the_canonical_payload() -> None:
    value: dict[str, object] = {"b": 2, "a": ("x",)}
    assert record_digest("KIND", value) == domain_digest("KIND", "1.0.0", canonical_payload(value))


def test_outcome_profile_record_fields() -> None:
    record = outcome_profile_record(*BUILTIN_OUTCOME_PROFILES[0])
    assert record.profile_ref == "REQUIREMENT_VERIFICATION_OUTCOME" and record.version == 1
    assert record.enabled is True and record.authority_owner == "OUTCOME_EVALUATION_OWNER"
    assert record.comparator_contract == "EXACT_BASELINE_SET_DIGEST_REQUIRED"
    assert record.closure_criteria == {
        "validity": "VALID_OR_PROFILE_ACCEPTED_LIMITED",
        "objective": "EXPLICITLY_ASSESSED",
        "residual_effect": "HANDLED_OR_ACCEPTED",
    }
    assert record.attribution_standard == "CONTRIBUTION_ONLY_WITHOUT_CAUSAL_DESIGN"


@pytest.mark.parametrize(
    ("windows", "digest", "missing"),
    [
        (({"assessment_phase": "INTERIM"},), BASELINE, ()),
        ((), BASELINE, ("assessment_windows",)),
        (({"assessment_phase": "INTERIM"},), "", ("comparison_baseline_set_digest",)),
        ((), "", ("assessment_windows", "comparison_baseline_set_digest")),
    ],
)
def test_missing_series_fields(
    windows: tuple[dict[str, object], ...], digest: str, missing: tuple[str, ...]
) -> None:
    assert missing_series_fields(windows, digest) == missing


def test_series_record_orders_ids_before_the_final_clock_call() -> None:
    ports = Ports()
    record = series(ports)
    assert ports.log == ["ids:outcome-series-revision", "ids:outcome-series", "clock"]
    assert record.revision == 0 and record.final_within_scope_state == "NOT_ASSESSED"
    assert record.phase_states == {
        "INTERIM": "WAITING_OBSERVATION",
        "FOLLOW_UP": "WAITING_OBSERVATION",
    }
    assert record.assessment_refs == () and record.observation_refs_by_phase == {}


def test_linked_phase_updates_do_not_mutate_the_series() -> None:
    current = series()
    before = current.model_dump()
    ready = linked_phase_updates(current, "INTERIM", ("span:1",), "PARTIAL")
    waiting = linked_phase_updates(current, "FOLLOW_UP", (), "COMPLETE")
    missing = linked_phase_updates(current, "FOLLOW_UP", ("span:1",), "MISSING")
    assert list(ready) == [
        "observation_refs_by_phase",
        "observation_completeness_by_phase",
        "phase_states",
    ]
    assert ready["phase_states"] == {
        "INTERIM": "READY_TO_ASSESS",
        "FOLLOW_UP": "WAITING_OBSERVATION",
    }
    assert ready["observation_refs_by_phase"] == {"INTERIM": ("span:1",)}
    assert ready["observation_completeness_by_phase"] == {"INTERIM": "PARTIAL"}
    assert cast(dict[str, str], waiting["phase_states"])["FOLLOW_UP"] == "WAITING_OBSERVATION"
    assert cast(dict[str, str], missing["phase_states"])["FOLLOW_UP"] == "WAITING_OBSERVATION"
    assert current.model_dump() == before


@pytest.mark.parametrize(
    ("completeness", "observed", "compared", "validity", "limits"),
    [
        ("CORRUPT", True, True, "INVALID", ["observation completeness is CORRUPT"]),
        ("COMPLETE", True, True, "VALID", []),
        ("COMPLETE", True, False, "INCONCLUSIVE", ["comparator evidence is missing"]),
        (
            "PARTIAL",
            True,
            True,
            "LIMITED",
            ["observation completeness is PARTIAL"],
        ),
        (
            "PARTIAL",
            False,
            False,
            "INCONCLUSIVE",
            ["comparator evidence is missing", "observation completeness is PARTIAL"],
        ),
        (
            "MISSING",
            True,
            True,
            "INCONCLUSIVE",
            ["observation completeness is MISSING"],
        ),
    ],
)
def test_judge_assessment(
    completeness: str, observed: bool, compared: bool, validity: str, limits: list[str]
) -> None:
    current = series()
    updated = current.model_copy(
        update={"observation_completeness_by_phase": {"INTERIM": completeness}}
    )
    result = judge_assessment(
        updated, "INTERIM", ("span:1",) if observed else (), ("span:2",) if compared else ()
    )
    assert result[0] == validity and result[1] == limits


def test_judge_assessment_reads_missing_completeness_and_attribution() -> None:
    current = series()
    assert judge_assessment(current, "INTERIM", ("span:1",), ("span:2",))[0] == "INCONCLUSIVE"
    assert judge_assessment(current, "INTERIM", ("span:1",), ("span:2",))[2] == "NOT_ASSESSED"
    planned = current.model_copy(update={"planned_execution_ref": "plan-execution:1"})
    assert judge_assessment(planned, "INTERIM", ("span:1",), ())[2] == "ASSOCIATED"
    assert judge_assessment(planned, "INTERIM", (), ())[2] == "NOT_ASSESSED"


def test_assessment_record_orders_ids_and_copies_the_profile() -> None:
    ports = Ports()
    current = series()
    limitations = ["one", "two"]
    record = assessment_record(
        current,
        profile(),
        "INTERIM",
        ("span:1",),
        ("span:2",),
        ("a",),
        "VALID",
        limitations,
        "ASSOCIATED",
        **ports_args(ports),
    )
    assert ports.log == [
        "ids:outcome-assessment-revision",
        "ids:outcome-assessment",
        "clock",
    ]
    assert record.limitations == ("one", "two") and record.validity == "VALID"
    assert record.profile_ref == "EXPERIMENT_LEARNING_OUTCOME" and record.profile_version == 1
    assert record.changed_dimensions == tuple(profile().effect_dimensions)
    assert record.lifecycle == "ASSESSED" and record.objective_attainment == "NOT_ASSESSED"
    assert limitations == ["one", "two"]


def test_assessed_series_updates() -> None:
    current = series()
    record = assessment(current)
    interim = assessed_series_updates(current, "INTERIM", record, "LIMITED")
    final = assessed_series_updates(current, "FINAL_WITHIN_SCOPE", record, "VALID")
    assert list(interim) == ["phase_states", "assessment_refs", "final_within_scope_state"]
    assert interim["assessment_refs"] == (record.outcome_assessment_id,)
    assert interim["final_within_scope_state"] == "NOT_ASSESSED"
    assert cast(dict[str, str], interim["phase_states"])["INTERIM"] == "ASSESSED"
    assert final["final_within_scope_state"] == "VALID"
    assert current.phase_states["INTERIM"] == "WAITING_OBSERVATION"


def test_reassessed_record_merges_evidence_and_keeps_the_input() -> None:
    current = assessment(series(), "INCONCLUSIVE", [])
    before = current.model_dump()
    ports = Ports()
    new_refs = ("span:1", "span:9", "span:9")
    record = reassessed_record(current, new_refs, "again", **ports_args(ports))
    assert ports.log == ["ids:outcome-assessment-revision", "clock"]
    assert record.actual_observation_refs == ("span:1", "span:9")
    assert record.changed_dimensions == ("evidence_coverage",) and record.validity == "LIMITED"
    assert record.limitations[-1] == "reassessment reason: again"
    assert record.supersedes_revision_digest == current.revision_digest
    assert record.outcome_assessment_id == current.outcome_assessment_id
    assert current.model_dump() == before
    same = reassessed_record(current, ("span:1",), "same", **ports_args(Ports()))
    assert same.changed_dimensions == ()


@pytest.mark.parametrize(
    ("method", "counterfactual", "evidence", "level"),
    [
        ("RANDOMIZED", ("c",), ("e",), "CAUSAL_ATTRIBUTION_SUPPORTED"),
        ("RANDOMIZED", (), ("e",), "ASSOCIATED"),
        ("CONTRIBUTION_ANALYSIS", (), ("e",), "CONTRIBUTION_SUPPORTED"),
        ("PROCESS_TRACING", (), (), "INCONCLUSIVE"),
        ("OTHER", (), ("e",), "ASSOCIATED"),
        ("OTHER", (), (), "INCONCLUSIVE"),
    ],
)
def test_attribution_support_levels(
    method: str, counterfactual: tuple[str, ...], evidence: tuple[str, ...], level: str
) -> None:
    supported, limitations = attribution_support(method, ("f",), counterfactual, evidence)
    assert supported == level
    insufficient = "causal attribution design/evidence is insufficient"
    assert (insufficient in limitations) is (level != "CAUSAL_ATTRIBUTION_SUPPORTED")
    _none, no_context = attribution_support(method, (), counterfactual, evidence)
    assert no_context[-1] == "contextual factors are not explicitly represented"


def test_attribution_record() -> None:
    ports = Ports()
    current = assessment(series())
    record = attribution_record(
        current,
        "CONTRIBUTION_ANALYSIS",
        (),
        (),
        ("span:1",),
        "CONTRIBUTION_SUPPORTED",
        ["x"],
        **ports_args(ports),
    )
    assert ports.log == ["ids:outcome-attribution", "clock"]
    assert record.supported_level == "CONTRIBUTION_SUPPORTED" and record.limitations == ("x",)
    assert record.outcome_assessment_id == current.outcome_assessment_id


@pytest.mark.parametrize("validity", ["INVALID", "LIMITED", "INCONCLUSIVE", "VALID"])
def test_change_set_record_restrictions(validity: str) -> None:
    ports = Ports()
    current = assessment(series(), validity, [])
    changes: dict[str, object] = {"CRITERION": {"a": 1}, "BASELINE": {"b": 2}}
    record = change_set_record(current, changes, "policy:p", PLAN, **ports_args(ports))
    assert ports.log == ["ids:outcome-change-set", "clock"]
    assert (record.validity_restrictions != ()) is (validity != "VALID")
    assert [item["entity_namespace"] for item in record.candidate_revisions] == [
        "CRITERION",
        "BASELINE",
    ]
    assert all(item["applied"] is False for item in record.candidate_revisions)
    assert record.impact_propagation_plan["stale_refs"] == ("CRITERION", "BASELINE")
    assert record.status == "PROPOSED_NOT_APPLIED" and record.proposed_entity_changes == changes


@pytest.mark.parametrize(
    ("evidence", "window", "design", "status"),
    [
        (("span:1",), "w", "QUASI", "CANDIDATE"),
        (("span:1",), "w", "TEMPORAL_ONLY", "INSUFFICIENT_CAUSAL_DESIGN"),
        ((), "w", "QUASI", "INSUFFICIENT_CAUSAL_DESIGN"),
        (("span:1",), "", "QUASI", "INSUFFICIENT_CAUSAL_DESIGN"),
    ],
)
def test_impact_record(evidence: tuple[str, ...], window: str, design: str, status: str) -> None:
    ports = Ports()
    record = impact_record(
        series(), window, "EXPERIMENT_LEARNING_OUTCOME", evidence, design, **ports_args(ports)
    )
    assert ports.log == ["ids:outcome-impact", "clock"]
    assert record.status == status
    assert (record.limitations == ()) is (status == "CANDIDATE")


def test_revised_records_link_the_previous_revision() -> None:
    current = series()
    ports = Ports()
    updates: dict[str, object] = {"final_within_scope_state": "VALID"}
    revised = revised_series_record(current, updates, **ports_args(ports))
    assert ports.log == ["ids:outcome-series-revision", "clock"]
    assert revised.revision == 1 and revised.supersedes_revision_digest == current.revision_digest
    assert (
        revised.final_within_scope_state == "VALID"
        and current.final_within_scope_state == "NOT_ASSESSED"
    )
    assert updates == {"final_within_scope_state": "VALID"}
    base = assessment(current)
    ports = Ports()
    again = revised_assessment_record(base, {"validity": "VALID"}, **ports_args(ports))
    assert ports.log == ["ids:outcome-assessment-revision", "clock"]
    assert again.supersedes_revision_digest == base.revision_digest and again.validity == "VALID"
    assert again.outcome_assessment_id == base.outcome_assessment_id and base.validity == "LIMITED"


@pytest.mark.parametrize("supersedes", [None, "d" * 64])
def test_commit_parts_share_the_actor_and_call_ids_once(supersedes: str | None) -> None:
    current = series()
    ports = Ports()
    snapshot, actor, revision = commit_parts(
        current,
        current.outcome_series_id,
        current.series_revision_id,
        current.revision_digest,
        supersedes,
        "outcome/seriesCreated",
        ("span:1",),
        ids=cast(Any, ports),
    )
    assert ports.log == ["ids:snapshot"]
    assert revision.actor is actor and actor.actor_id == "agent:outcome-evaluator"
    assert snapshot.content == current.model_dump(mode="python")
    assert revision.snapshot_id == snapshot.snapshot_id and revision.entity_id == snapshot.entity_id
    assert revision.parent_revision_digests == (() if supersedes is None else (supersedes,))
    assert revision.evidence_refs == ("span:1",) and revision.affected_refs == ()
    assert revision.reason == "outcome/seriesCreated" and revision.created_at == current.created_at
    assert snapshot.content_digest == record_digest("ENTITY_SNAPSHOT", snapshot.content)

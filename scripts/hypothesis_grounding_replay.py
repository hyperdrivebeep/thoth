"""Offline grading of labelled grounding cases, not a production grounding implementation.

The scorer checks supplied structured facts and an independently labelled context. It neither
infers natural-language entailment nor creates links, changes records, or calls a model.
Usage: python scripts/hypothesis_grounding_replay.py check-fixtures
       python scripts/hypothesis_grounding_replay.py grade --predictions predictions.json
Predictions must be a JSON object from case ID to Proposal (see the fixture README).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

Role = Literal["OBSERVATION_RESTATEMENT", "EVIDENCE_INFERENCE", "HYPOTHESIS_ONLY", "UNASSESSED"]
Origin = Literal["SYNTHETIC", "MEASURED", "UNASSESSED"]
FIXTURES = Path(__file__).resolve().parents[1] / "tests/fixtures/hypothesis_grounding"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Binding(FrozenModel):
    span_id: str = Field(min_length=1)
    source_version_id: str = Field(min_length=1)
    text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Observation(FrozenModel):
    result_id: str
    subject_id: str
    condition: dict[str, str]
    numerator: int = Field(ge=0, strict=True)
    denominator: int = Field(gt=0, strict=True)
    value: Decimal
    unit: str
    observed_at: AwareDatetime
    synthetic: bool = Field(strict=True)

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("observation value must be finite")
        return value


class Span(FrozenModel):
    binding: Binding
    project_id: str
    observation: Observation
    exact_text: str
    relation: Literal["SUPPORT", "COUNTER", "REFERENCE_ONLY"]


class ReplayContext(FrozenModel):
    kind: Literal["OBSERVATION", "INFERENCE", "FUTURE", "LEGACY"]
    project_id: str
    cutoff_at: AwareDatetime
    data_origin: Origin
    target: Observation | None = None
    evidence: tuple[Span, ...] = ()
    permitted_span_ids: tuple[str, ...] = ()
    pinned_sources: tuple[Binding, ...] = ()
    legacy_basis: tuple[Binding, ...] = ()
    legacy_knowledge_state: Literal["PREDICTED", "UNKNOWN"] | None = None
    legacy_expected: str | None = None
    # Independently labelled supporting premises, never chosen by the proposal being graded.
    inference_support_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def unambiguous_context(self) -> ReplayContext:
        for identities in (
            [span.binding.span_id for span in self.evidence],
            [binding.span_id for binding in self.pinned_sources],
            [binding.span_id for binding in self.legacy_basis],
            list(self.permitted_span_ids),
            list(self.inference_support_ids),
        ):
            if len(set(identities)) != len(identities):
                raise ValueError("duplicate context identity")
        if self.kind == "OBSERVATION" and self.target is None:
            raise ValueError("observation context requires a target")
        if self.kind == "LEGACY" and (
            self.legacy_knowledge_state is None
            or (self.legacy_knowledge_state == "UNKNOWN") != (self.legacy_expected is None)
        ):
            raise ValueError("legacy context requires the original prediction state and text")
        return self


class Proposal(FrozenModel):
    """Replay assessment envelope; bindings/verification_claim are not model-granted authority."""

    schema_version: Literal["grounding_replay_proposal.v1"] = "grounding_replay_proposal.v1"
    role: Role
    knowledge_state: Literal["PREDICTED", "UNKNOWN"]
    expected: str | None = Field(default=None, min_length=1, max_length=80)
    rationale: str = Field(default="", max_length=500)
    basis: tuple[Binding, ...] = Field(default=(), max_length=5)
    observation: Observation | None = None
    data_origin: Origin
    verification_claim: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def valid_prediction_state(self) -> Proposal:
        if (self.knowledge_state == "UNKNOWN") != (self.expected is None):
            raise ValueError("prediction state conflicts with expected text")
        if self.expected is not None and not self.expected.strip():
            raise ValueError("empty prediction text")
        return self


class Score(FrozenModel):
    passed: bool
    reasons: tuple[str, ...]
    false_verification_promotions: int
    unauthorized_links: int
    invalid_links: int
    missing_observation_links: int


def link_problems(context: ReplayContext, binding: Binding) -> tuple[str, ...]:
    spans = {span.binding.span_id: span for span in context.evidence}
    pinned = {item.span_id: item for item in context.pinned_sources}
    problems: list[str] = []
    span = spans.get(binding.span_id)
    if binding.span_id not in context.permitted_span_ids:
        problems.append("UNAUTHORIZED_LINK")
    if span is None:
        problems.append("UNKNOWN_SPAN")
    else:
        if span.project_id != context.project_id:
            problems.append("FOREIGN_PROJECT_LINK")
        if binding != span.binding or binding != pinned.get(binding.span_id):
            problems.append("PINNED_SOURCE_MISMATCH")
        if hashlib.sha256(span.exact_text.encode("utf-8")).hexdigest() != binding.text_sha256:
            problems.append("TEXT_HASH_MISMATCH")
        if span.observation.observed_at > context.cutoff_at:
            problems.append("AFTER_CUTOFF")
        if context.kind != "LEGACY" and span.relation != "SUPPORT":
            problems.append("NON_SUPPORTING_EVIDENCE")
    return tuple(problems)


def score_row(context: ReplayContext, proposal: Proposal) -> Score:
    """Pure comparison with labelled facts. A passing proposal remains unverified."""
    reasons: list[str] = []
    unauthorized = invalid = 0
    for binding in proposal.basis:
        problems = link_problems(context, binding)
        reasons.extend(problems)
        invalid += bool(problems)
        unauthorized += any(p in problems for p in ("UNAUTHORIZED_LINK", "FOREIGN_PROJECT_LINK"))
    ids = {binding.span_id for binding in proposal.basis}
    if len(ids) != len(proposal.basis):
        reasons.append("DUPLICATE_LINK")
    promotions = int(proposal.verification_claim)
    if proposal.verification_claim:
        reasons.append("UNAUTHORIZED_VERIFICATION_PROMOTION")
    if proposal.data_origin != context.data_origin:
        reasons.append("DATA_ORIGIN_MISMATCH")
        if proposal.data_origin == "MEASURED":
            promotions = 1
    if proposal.role != "UNASSESSED" and not proposal.rationale.strip():
        reasons.append("GROUNDING_RATIONALE_REQUIRED")
    eligible: set[str] = (
        {
            span.binding.span_id
            for span in context.evidence
            if span.observation == context.target and not link_problems(context, span.binding)
        }
        if context.kind == "OBSERVATION"
        else set()
    )
    missing = int(bool(eligible) and not ids)
    if eligible:
        if proposal.role != "OBSERVATION_RESTATEMENT":
            reasons.append("OBSERVATION_ROLE_REQUIRED")
        if not ids:
            reasons.append("MISSING_REQUIRED_OBSERVATION_LINK")
        elif not ids <= eligible:
            reasons.append("OBSERVATION_LINK_MISMATCH")
        if proposal.observation != context.target or proposal.knowledge_state != "PREDICTED":
            reasons.append("OBSERVATION_CONTENT_MISMATCH")
    elif context.kind == "INFERENCE":
        if proposal.role != "EVIDENCE_INFERENCE":
            reasons.append("INFERENCE_ROLE_REQUIRED")
        if not ids or not ids <= set(context.inference_support_ids):
            reasons.append("INFERENCE_PREMISE_MISMATCH")
        if proposal.observation is not None:
            reasons.append("INFERENCE_IS_NOT_AN_OBSERVATION")
        if proposal.knowledge_state != "PREDICTED":
            reasons.append("INFERENCE_PREDICTION_REQUIRED")
    elif context.kind == "FUTURE":
        role = "HYPOTHESIS_ONLY" if proposal.knowledge_state == "PREDICTED" else "UNASSESSED"
        if proposal.role != role or ids or proposal.observation is not None:
            reasons.append("UNSUPPORTED_FUTURE_GROUNDING")
    elif context.kind == "LEGACY":
        if proposal.role != "UNASSESSED" or proposal.observation is not None:
            reasons.append("LEGACY_ROLE_MUST_REMAIN_UNASSESSED")
        if proposal.basis != context.legacy_basis:
            reasons.append("LEGACY_BASIS_CHANGED")
        if (proposal.knowledge_state, proposal.expected) != (
            context.legacy_knowledge_state,
            context.legacy_expected,
        ):
            reasons.append("LEGACY_PREDICTION_CHANGED")
    else:
        if proposal.role != "UNASSESSED" or ids or proposal.observation is not None:
            reasons.append("UNASSESSED_REQUIRED")
    return Score(
        passed=not reasons,
        reasons=tuple(sorted(set(reasons))),
        false_verification_promotions=promotions,
        unauthorized_links=unauthorized,
        invalid_links=invalid,
        missing_observation_links=missing,
    )


class Example(FrozenModel):
    case_id: str
    category: str
    context: ReplayContext
    accepted: Proposal
    rejected: Proposal


class ReplaySuite(FrozenModel):
    schema_version: Literal["grounding_replay_cases.v1"]
    examples: tuple[Example, ...] = Field(min_length=1)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check-fixtures", "grade"))
    parser.add_argument("--cases", type=Path, default=FIXTURES / "cases.json")
    parser.add_argument("--predictions", type=Path)
    args = parser.parse_args(argv)
    try:
        source = args.cases.read_bytes()
        suite = ReplaySuite.model_validate_json(source)
    except (OSError, ValueError):
        parser.error("invalid or unreadable replay case suite")
    if len({case.case_id for case in suite.examples}) != len(suite.examples):
        parser.error("duplicate case ID")
    results: dict[str, object] = {}
    if args.command == "check-fixtures":
        passed = True
        for case in suite.examples:
            good = score_row(case.context, case.accepted)
            bad = score_row(case.context, case.rejected)
            passed = passed and good.passed and not bad.passed
            results[case.case_id] = {"accepted": good.model_dump(), "rejected": bad.model_dump()}
    else:
        if args.predictions is None:
            parser.error("grade requires --predictions")
        try:
            raw = json.loads(
                args.predictions.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs
            )
        except (OSError, ValueError):
            parser.error("invalid, duplicate-key, or unreadable predictions")
        if not isinstance(raw, dict):
            parser.error("predictions must be an object")
        predictions = cast(dict[str, object], raw)
        if set(predictions) != {case.case_id for case in suite.examples}:
            parser.error("predictions must cover every case exactly once")
        passed = True
        for case in suite.examples:
            try:
                proposal = Proposal.model_validate(predictions[case.case_id])
            except ValueError:
                parser.error(f"invalid proposal for {case.case_id}")
            result = score_row(case.context, proposal)
            passed = passed and result.passed
            results[case.case_id] = result.model_dump()
    print(
        json.dumps(
            {
                "scope": "OFFLINE_LABELLED_REPLAY",
                "case_set_sha256": hashlib.sha256(source).hexdigest(),
                "passed": passed,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

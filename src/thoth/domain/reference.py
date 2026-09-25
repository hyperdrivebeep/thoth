"""Bounded reference inquiries are annotations, never official targets or evaluator inputs."""

import ast
import json
from decimal import ROUND_HALF_EVEN, Context, Decimal, DecimalException, localcontext
from typing import Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest

CONTEXT_FIELDS = (
    "metric_definition",
    "formula",
    "unit",
    "denominator",
    "population",
    "environment",
    "time_window",
    "measurement_method",
)


class ReferenceError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ReferenceContext(DomainModel):
    metric_definition: str | None = Field(default=None, max_length=2000)
    formula: str | None = Field(default=None, max_length=2000)
    unit: str | None = Field(default=None, max_length=80)
    denominator: str | None = Field(default=None, max_length=2000)
    population: str | None = Field(default=None, max_length=2000)
    environment: str | None = Field(default=None, max_length=2000)
    time_window: str | None = Field(default=None, max_length=2000)
    measurement_method: str | None = Field(default=None, max_length=2000)

    @field_validator("*", mode="before")
    @classmethod
    def trim_context(cls, value: object) -> object:
        return value.strip() or None if isinstance(value, str) else value

    def missing(self) -> tuple[str, ...]:
        values = self.model_dump()
        return tuple(name for name in CONTEXT_FIELDS if not values[name])


class ReferenceMeasurement(DomainModel):
    span_id: str = Field(min_length=1, max_length=200)
    value: Decimal = Field(allow_inf_nan=False)
    context: ReferenceContext
    quotes: dict[str, str] = Field(max_length=12)
    field_span_ids: dict[str, str] = Field(default_factory=dict, max_length=8)
    value_quote: str = Field(min_length=1, max_length=4000)
    variable: str | None = Field(default=None, max_length=100)

    @field_validator("quotes", "field_span_ids")
    @classmethod
    def known_context_fields(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) - set(CONTEXT_FIELDS):
            raise ValueError("REFERENCE_CONTEXT_FIELD_UNKNOWN")
        return value

    @field_validator("value")
    @classmethod
    def bound_number(cls, value: Decimal) -> Decimal:
        parts = value.as_tuple()
        if (
            len(parts.digits) > 128
            or not isinstance(parts.exponent, int)
            or abs(parts.exponent) > 1024
        ):
            raise ValueError("REFERENCE_NUMBER_LIMIT")
        return value


class ReferenceScenario(DomainModel):
    scenario_id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    measurement_span_ids: tuple[str, ...] = Field(min_length=1, max_length=64)


class ReferenceRequest(DomainModel):
    criterion_id: str = Field(min_length=1, max_length=200)
    expected_revision_digest: str = Field(min_length=64, max_length=64)
    lane: Literal["REFERENCE_RANGE_CANDIDATE", "RECALCULATED_VALUE"]
    source_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    calculator_id: str = Field(min_length=1, max_length=100)
    calculator_version: str = Field(min_length=1, max_length=40)
    target: ReferenceContext
    measurements: tuple[ReferenceMeasurement, ...] | None = Field(default=None, max_length=64)
    inquiry_id: str | None = Field(default=None, max_length=200)
    evaluator_binding_digest: str | None = Field(default=None, min_length=64, max_length=64)
    scenarios: tuple[ReferenceScenario, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def scenario_scope(self) -> Self:
        if self.scenarios and self.lane != "REFERENCE_RANGE_CANDIDATE":
            raise ValueError("REFERENCE_SCENARIO_LANE_INVALID")
        if len({s.scenario_id for s in self.scenarios}) != len(self.scenarios):
            raise ValueError("REFERENCE_SCENARIO_ID_DUPLICATE")
        return self


class ReferenceAnswerRequest(DomainModel):
    criterion_id: str = Field(min_length=1, max_length=200)
    expected_revision_digest: str = Field(min_length=64, max_length=64)
    inquiry_id: str = Field(min_length=1, max_length=200)
    question_id: str = Field(min_length=1, max_length=200)
    expected_answer_revision: int = Field(default=0, ge=0)
    value: str = Field(min_length=1, max_length=2000)


class ReferenceQuestion(DomainModel):
    question_id: str
    field: str
    basis_revision_digest: str
    prompt: str


class ReferenceAnswer(DomainModel):
    question_id: str
    field: str
    revision: int = Field(ge=1)
    value: str
    actor_ref: str
    session_ref: str | None
    answered_at: AwareDatetime
    provenance: Literal["HUMAN_ASSERTION"] = "HUMAN_ASSERTION"


class ReferenceSuggestedAnswer(DomainModel):
    field: str = Field(max_length=80)
    value: str = Field(min_length=1, max_length=2000)
    quote: str = Field(min_length=1, max_length=4000)


class ReferenceMappingProposal(DomainModel):
    measurements: tuple[ReferenceMeasurement, ...] = Field(default=(), max_length=64)
    answers: tuple[ReferenceSuggestedAnswer, ...] = Field(default=(), max_length=3)
    missing_field_order: tuple[str, ...] = Field(default=(), max_length=8)
    scenarios: tuple[ReferenceScenario, ...] = Field(default=(), max_length=4)


class ReferenceMappingTrace(DomainModel):
    model_id: str
    prompt_version: str
    input_digest: str
    output_digest: str
    input_head_set_digest: str
    project_revision: int
    policy_ref: str
    scripted: bool


class ReferenceCalculation(DomainModel):
    calculator_id: str
    calculator_version: str
    input_digest: str
    output_digest: str
    normalized_values: tuple[str, ...]
    value: str | None = None
    lower: str | None = None
    upper: str | None = None
    unit: str
    operations: tuple[str, ...]
    variables: dict[str, str] = Field(default_factory=dict, max_length=64)
    source_refs: tuple[str, ...]
    uncertainty: str
    statistical_interval: Literal[False] = False


class ReferenceScenarioResult(DomainModel):
    scenario: ReferenceScenario
    state: Literal["CALCULATED", "HOLD"]
    calculation: ReferenceCalculation | None = None
    reason_codes: tuple[str, ...] = ()
    lower_delta_from_base: str | None = None
    upper_delta_from_base: str | None = None


class ReferenceInquiryPayload(DomainModel):
    inquiry_id: str
    project_id: str
    thread_id: str
    criterion_id: str
    criterion_basis_digest: str
    original_revision_digest: str
    lane: Literal["REFERENCE_RANGE_CANDIDATE", "RECALCULATED_VALUE"]
    revision: int = Field(ge=1, le=128)
    state: Literal["NEEDS_INPUT", "HOLD", "CALCULATED", "STALE"]
    source_refs: tuple[str, ...] = Field(max_length=64)
    source_digests: dict[str, str] = Field(default_factory=dict, max_length=64)
    calculator_id: str
    calculator_version: str
    evaluator_binding_digest: str | None = None
    target: ReferenceContext
    measurements: tuple[ReferenceMeasurement, ...] = Field(default=(), max_length=64)
    questions: tuple[ReferenceQuestion, ...] = Field(default=(), max_length=8)
    answers: tuple[ReferenceAnswer, ...] = Field(default=(), max_length=128)
    calculation: ReferenceCalculation | None = None
    scenarios: tuple[ReferenceScenario, ...] = Field(default=(), max_length=4)
    scenario_results: tuple[ReferenceScenarioResult, ...] = Field(default=(), max_length=4)
    reason_codes: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    mapping_traces: tuple[ReferenceMappingTrace, ...] = Field(default=(), max_length=128)
    question_order: tuple[str, ...] = Field(default=(), max_length=8)
    actor_ref: str
    session_ref: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    authorization_state: Literal["NOT_AUTHORIZED"] = "NOT_AUTHORIZED"
    evaluator_input_allowed: Literal[False] = False
    semantic_truth_certified: Literal[False] = False


class ReferenceInquiry(ReferenceInquiryPayload):
    inquiry_digest: str

    @model_validator(mode="after")
    def sealed(self) -> Self:
        expected = domain_digest(
            "REFERENCE_INQUIRY",
            "1.0.0",
            canonical_payload(self.model_dump(mode="python", exclude={"inquiry_digest"})),
        )
        if expected != self.inquiry_digest:
            raise ValueError("REFERENCE_INQUIRY_DIGEST_MISMATCH")
        if (self.state == "CALCULATED") != (self.calculation is not None):
            raise ValueError("REFERENCE_CALCULATION_STATE_MISMATCH")
        return self


def seal_inquiry(values: dict[str, object]) -> ReferenceInquiry:
    # Validate defaults before sealing, then validate the final immutable envelope.
    draft = ReferenceInquiryPayload.model_validate(values)
    payload = json.loads(
        canonical_payload(draft.model_dump(mode="python", exclude={"inquiry_digest"}))
    )
    return ReferenceInquiry.model_validate(
        {
            **payload,
            "inquiry_digest": domain_digest(
                "REFERENCE_INQUIRY", "1.0.0", canonical_payload(payload)
            ),
        }
    )


def criterion_reference_basis(values: dict[str, object]) -> str:
    excluded = {
        "criterion_revision_id",
        "revision_digest",
        "supersedes_revision_digest",
        "receipt_ref",
        "created_at",
        "reference_inquiry",
        "result_and_uncertainty",
        "schema_version",
    }
    return domain_digest(
        "REFERENCE_CRITERION_BASIS",
        "1.0.0",
        canonical_payload({key: value for key, value in values.items() if key not in excluded}),
    )


def evaluate_reference_formula(expression: str, variables: dict[str, Decimal]) -> Decimal:
    if len(expression) > 4096 or len(variables) > 64:
        raise ReferenceError("REFERENCE_FORMULA_LIMIT")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError):
        raise ReferenceError("REFERENCE_FORMULA_UNSUPPORTED") from None
    if len(tuple(ast.walk(tree))) > 256 or any(not v.is_finite() for v in variables.values()):
        raise ReferenceError("REFERENCE_FORMULA_LIMIT")

    def visit(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            literal = ast.get_source_segment(expression, node)
            if literal is None or literal.lower().startswith(("0x", "0o", "0b")):
                raise ReferenceError("REFERENCE_FORMULA_UNSUPPORTED")
            return Decimal(literal.replace("_", ""))
        if isinstance(node, ast.Name) and node.id in variables:
            return variables[node.id]
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
        raise ReferenceError("REFERENCE_FORMULA_UNSUPPORTED")

    try:
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN, Emin=-1024, Emax=1024)):
            result = +visit(tree)
        if not result.is_finite():
            raise ReferenceError("REFERENCE_ARITHMETIC_INVALID")
        return result
    except (DecimalException, RecursionError, ValueError) as exc:
        if isinstance(exc, ReferenceError):
            raise
        raise ReferenceError("REFERENCE_ARITHMETIC_INVALID") from None

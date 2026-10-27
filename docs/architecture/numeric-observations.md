# Arithmetic from printed source numbers

The `checked_observations` result envelope (1.0.0, `SOURCE_ARITHMETIC`) records
deterministic arithmetic over an admitted source input. Its fixed model contract is:

> Values calculated from source numbers; not a causal or computational-complexity determination.

This is a derived observation, not original evidence, a verified experimental condition,
a new official criterion or a `ReferenceInquiry`. Existing evidence and answer text remain intact.

## Observation and arithmetic

`numeric_series_observation` consumes the scoped artifact reader and actual connected evidence.
It accepts repeated rows with an exact operation label, numeric size, size label, value and
registered unit in one observed paragraph or closed fenced block. The complete block and its
observed heading/preamble must be present. Mixed prose, incomplete or uncertain boundaries,
duplicate sizes, mixed units and unsupported numeric literals produce diagnostics without a
calculation for the affected group. CSV and cross-series comparisons are not supported.

Grouping uses the source version, structural region, exact operation/size labels and unit.
Labels such as `first fill_nan` and `fill_nan` remain distinct. Position describes grouping;
it does not certify experimental equivalence. Printed zero and rounded measurements are not
certifications of the true measured value.

`DocumentCalculationService` receives `DocumentCalculatorPort` from app composition.
`DocumentCalculatorRegistry` registers versioned calculators. The default adjacent-series
adapter reuses `UnitRegistry` and its sorted unit manifest without changing reference calculations.
It sorts sizes numerically and calculates only neighboring ratios, differences and finite-interval
log-log slopes. Arithmetic uses Decimal precision 28 and ROUND_HALF_EVEN; display values have
six decimal places. Division by zero and invalid logarithm domains remain `NOT_CALCULATED`.

## Identity and storage

| Identifier | Meaning |
|---|---|
| `series_digest` | Content identity: original byte hash, parser/observer/grammar/calculator/unit versions, stable region/context positions and exact labels. No upload, node, project, request or clock identity. |
| `points_digest` | Observed points in original source order, exact numeric text, Decimal strings, quote hashes and stable source coordinates. |
| `binding_digest` | Current request/project/source version, actual source references, content/point digests and observed source states. |
| calculation `input_digest` | All three digests, unit manifest, operands, operation, calculator version and precision/rounding. |
| calculation `output_digest` | Exact computed value, six-place display value or explicit failure reason. |

`NumericSeriesObservation` and `DerivedCalculation` are typed, versioned members of the envelope.
Operand/source membership, series binding, operation/version input digest and output digest are
checked. Publication rechecks request and actual evidence binding inside the existing result UoW,
before writes. Existing request-head, lease, resource-scope, currentness and publication guards apply.
There is no new canonical table, evidence revision, result manifest version or source digest algorithm.

The full record belongs to the existing result. SUMMARY reads derive display rows; FULL reads
retain the arithmetic/provenance payload. Older results without the field acquire no calculations,
successful check claim or backfill. Unknown payload versions do not become valid arithmetic.

## Model and user views

Only SEMANTIC_REVIEWER, REVIEW_ADJUDICATOR and HYPOTHESIS_GENERATOR receive the compact numeric
view. Every admitted series must still match the exact source IDs, versions, hashes and locators
in that role's actual input. Source quotes are never removed to fit calculations. Arithmetic uses
only the remainder of the existing retrieval character budget; omitted calculations and reasons
are recorded per role. Existing span and structural limits remain unchanged. No extra model call
is introduced. Compact byte counts are not token or billed-cost measurements.

SEMANTIC_REVIEWER and HYPOTHESIS_GENERATOR, and only when the compact view was actually
delivered to them, also receive one fixed instruction in a separate `numeric_instruction` field:

> When checked_observations are present, cite the step ratios and log-log slopes of the series that bear on the question as observed arithmetic with their operand refs, and compare slopes between series where relevant; do not state a cause or a complexity class from them.

The adjudicator receives the view without the instruction. A run without a delivered view carries
neither the view nor the instruction. The instruction is a fixed sentence outside the character budget, like the other fixed
instructions; the view alone is admitted within the budget.

Model views share series labels and carry raw operand span references. The derived record's
calculation ID is not added to the original evidence-ID enum. The model input includes the fixed
contract, precision, calculator/grammar/unit versions and grouping/measurement limitations.

The model-only `interval-display:1.0.0` projection sends one `intervals` row per adjacent pair:
`series_index`, `sizes`, one pair of `operand_refs`, and `ratio`, `difference`, `log_log_slope`.
Successful cells contain six-decimal display strings; only unsuccessful cells contain a
`status`/`reason` object. Difference units come from the series; ratios and slopes are dimensionless.
Full precision remains in the stored calculation records and is not duplicated in the model input.
Context labels remain complete. Admission includes or excludes a whole interval at a time,
while delivery counts still count individual calculations, including explicit failed calculations.
The projection version participates in the existing context/stage basis; stored envelope and
calculation versions and digests are unchanged.

The collapsed result section is labelled “원문 숫자로 계산한 값” / “Values calculated from source
numbers”. It shows intervals, operations, display values and source buttons that use the existing
evidence-detail route. It also remains available when arithmetic was saved but no answer exists.
Unknown units, unsupported groups and undelivered rows are not presented as successful checks.

Relevant tests: `test_numeric_series`, `test_numeric_series_pipeline`,
`test_numeric_citation_instruction`, `test_model_instruction_sentences` and
`CheckedObservations.test.tsx`.

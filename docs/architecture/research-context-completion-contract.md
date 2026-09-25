# Research context and completion — accepted target contract

Status: DESIGN_LOCKED; implementation and live acceptance are separate.
Decision date:2026-09-15. Research input: external review v2 with v2.1 semantic corrections.
Execution specification: ../plans/THOTH_RESEARCH_TO_IMPLEMENTATION_WORK_ORDER_20260915.md.

Implementation observation2026-09-16: W0–W6 local consumer paths and limited checks are recorded in
`../verification/research-context-implementation-20260915.md`. W7's one normal MobileNets run stored
planner/reranker stages and the required table context, but produced no answer because the reviewer
request exceeded the unchanged byte cap. A lossless schema-sharing correction is locally verified;
its subsequent live acceptance is NOT_RUN. This observation does not alter the locked contract or
declare full product/owner10 completion.

Subsequent user-authorized correction2026-09-16 removes the model-dispatch180000-byte cutoff and
the generic compressor after its literal-data counterexample. Non-model resource bounds remain.
See `../verification/model-input-admission-20260916.md`; the previous live result is preserved and
this later correction has local verification only. Token-aware admission remains deferred.

## Preserve existing owners

ResearchBudget, source/structure/evidence ledger, typed EvidenceRequirement, criterion authority,
research revisions/receipts, project-isolated Memory and operation ownership remain authoritative.
No new independent canonical DB or secondary-facet truth is required by this change.
Existing historical operation/manifests/receipts retain their original interpretation.

## Runtime boundary

Overall budget and transport-phase causes are separate concepts. The existing900-second/24-call
policy remains a local default, not a demonstrated optimum or a proven external provider limit.
Phase controls belong inside the transport contract; no mandatory eight-field settings UI.
Restart derives a process-local monotonic wait from persisted remaining budget; it does not reset
allowance. Local cancellation preserves observations and does not prove remote termination.

## Source context

Parser selection and capabilities are registry/adapter concerns, not vendor/format branches in
application code. Actual nodes/relations/locators/coverage, not a capability label alone, determine
available evidence. Preserve exact source version and original bytes through reparse and expansion.
Caption/header/unit/footnote/mention context must be obtainable for relevant table/row/cell hits.
Unavailable structure restricts dependent judgments only; valid text evidence and authorized local
source exploration continue. No silent downgrade from structured extraction to equivalent-looking text.

## Evidence gaps and autonomy

Connected-only prohibits external acquisition, not internal expansion of authorized source material.
Gap reconciliation binds a concrete requirement and current source/criterion basis. AI proposes
semantic resolution or non-relevance; code verifies explicit structural relations, identity, digest,
scope and authority, and consumes the required adjudication. A BOUND obligation cannot be waived by
an optional check. Initial ranker warnings neither automatically settle a final HOLD nor disappear
merely because the next model omits them. Ordinary exploration/review adds no blanket human approval.

## Role context and result lifetime

Each role receives necessary actual text and conditions, not just hashes, and avoids unnecessary
copies of prior narrative. Reuse requires a compatible exact input/validation basis and current
authorization. Stage persistence reuses existing records first; any necessary new typed record lives
in the same revision/receipt system with atomic publication and codec/version validation.
Incomplete or late model output is not a completed stage and cannot overwrite current results.

## Completion and deferred decisions

Execution/persistence success is not answer completeness. New read information should distinguish
not produced, partial, HOLD, assessed-for-request and legacy unknown; no scientific truth guarantee
is inferred from an operation status. Report checkpoint/resume support accurately.
Automatic new budget, true same-attempt resume after terminal closure, overall-policy tuning and
model/effort changes remain outside the locked implementation scope until separately authorized.

## Acceptance boundary

Related unit/contract/integration tests plus normal-entry actual-source QA must demonstrate the
consumer chain. Required evidence and legitimate abstention matter more than catalog/test counts.
Research evaluator limitations are not product gates; its historical FAIL is not relabelled PASS.
No claimed speed, correctness or complete-product result before measurement.

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

## Interrupted model calls and resume

A model call that stops the research early (cut off, slowed to a crawl, over its per-call time, or generating without end) is
reported as a model-call failure with no answer; the operation itself stays a published HOLD.

- **Per-call limits (Codex transport).** `TransportTimeouts` carries `dispatch_total_seconds` and the
  stall limits (`stall_min_events_per_second`, `stall_warmup_seconds`, `stall_window_seconds`, `stall_sustain_seconds`).
  Unset values take the adapter defaults in `stream_pace.py` (measured, see
  local controlled checks). Output events are counted from the first one; nothing
  is judged before it. A stall ends the call as `OAUTH_STALLED_STREAM_REMOTE_STOP_UNKNOWN`, the total limit as
  `OAUTH_DISPATCH_DEADLINE_REMOTE_STOP_UNKNOWN`. There is still no whole-research clock.
  The runaway limits (`runaway_max_output_events`, `runaway_visible_bytes_per_token` with a floor
  `runaway_min_visible_bytes`, `runaway_blank_run`) end an output that never finishes as
  `OAUTH_RUNAWAY_OUTPUT_REMOTE_STOP_UNKNOWN`; the observation records which limit was crossed.
- **Resume is the user's action.** `thread/input` with `resume_from_operation_id` asks the same question
  again as a new operation; it must name the latest finished run of that thread. A completed stage
  of that run is reused only when this run's input basis for the stage (everything except the run's
  own names: request revision, project head set, memory pack id and time) equals the stored one.
  Any change of sources, scope, settings, policy, cutoff or behavior changes the digest and the
  stage is called again. A reused stage is a new stage record with `reused_from`, no dispatch ids and
  no usage; `thread/read` reports `stage_reuse` (reused / new). Nothing is reused without the request.
- Stream pace observations use canonical decimal values for rates and elapsed-limit seconds.
  Their JSON representation is decimal text, as with transport timeout controls; older numeric
  observations remain readable. Recording an interrupted dispatch preserves its diagnostic
  instead of failing while serializing the observation.
- **Automatic retry is a project setting, on by default (user decision 20261010-01).** `model/callSettings/read|update`
  (`auto_retry_interrupted_model_call`, digest-checked; the default is one line in
  `domain/model_call_settings.py`). When on, a cut-off, slowed, over-long or runaway call (not a 429,
  not an authentication refusal, not a format error, not a usage limit) is sent again as
  `retry_of_dispatch_id`, at most twice per call, waiting about 2 s and then 4 s (each shaken by up to
  25%). The transient-429 retry happens at most once and shares the limit of two. Usage counts every
  send. An existing saved choice, including false, is preserved; only a project without a saved
  choice uses the default. The server cannot say whether the cut-off request stopped, so retries
  may repeat remote work. With the switch off, the user continues with "이어서 조사" instead.
- **A judgement review is never reused.** `NON_REUSABLE_ROLES` (`research_stage_reuse.py`) keeps
  `REVIEW_ADJUDICATOR` out of resume: its conflict and gap decisions carry the requirement-set digest
  of the run that made them, so a reused one would be discarded as stale.

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

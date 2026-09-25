# Explicit final producer selection for recovery results

2026-09-25 correction contract. Implementation and focused acceptance are pending.

The existing history/currentness contract remains: only an exact CURRENT result is
returned as `current_result`; other readable results remain `previous_result` with their
currentness reasons. A partial research result may have complete provenance, while a
terminal operation may still have unknown provenance. Completion, content quality,
learning disposition and provenance coverage are separate fields.

## Observed failure and actual producer

The A04 recovery call appends both its PENDING learning revision and its final HELD
learning revision to `work.record_refs`. Both have valid transition receipts and the
same request, Outcome and learning basis. The generic capture currently treats the two
digests for one entity key as unresolved, even though the final ref was explicitly
returned by that recovery call's successful save. It omits only that learning key from
`produced_final_heads` and marks the terminal recovery UNKNOWN_BASIS.

The observed final state is `HELD / MEMORY_REVIEW_HELD`, with one committed memory and
eight held memories. It must not be relabeled COMMITTED or fully verified research.

## Selection contract

- A writer may nominate a final producer only from the exact `RevisionRef` returned by
  its own successful final publication, or from a validated existing final checkpoint
  that the call explicitly returns. Nomination is scoped to the current ResearchWork.
- Retain intermediate PENDING and final refs and their producer receipts in provenance.
  Do not delete historical refs merely to make each entity key have one digest.
- The generic capture may resolve multiple recorded digests only when the explicit
  selected ref belongs to that exact project/entity/revision, is among the committed
  produced refs and is backed by its transition receipt. No explicit valid selection
  means `BASIS_FINAL_PRODUCER_UNRESOLVED`, as before.
- Validate the selected ref against the actual head at publication. A later writer's
  head is never a substitute. Invalid/missing receipts, foreign refs, inconsistent
  basis or a changed head continue to prevent CURRENT.
- Recovery merges prior basis evidence without replacing a newly validated final
  selection with an older checkpoint's selection. It preserves unrelated ambiguity,
  legacy UNKNOWN and other existing reasons; it does not blanket-clear diagnostics or
  force coverage COMPLETE.
- The new result may have complete provenance when all its actual producer/consumer
  bindings are proved, while remaining `research_state=PARTIAL`,
  `answer_status=PARTIAL_HOLD` and learning state HELD. Unreplayed improvement/baseline
  work stays explicitly unverified.
- This is publication-time capture. Read queries remain pure and do not repair or
  rewrite stored results. Old manifests, producer receipts and source bytes remain
  unchanged. No model or external execution is repeated to recover this selection.

Normal post-execution learning, same-key reentry, paused recovery and post-commit memory
recovery use the same explicit selection rule. Actor, lease, request fence, committed
Outcome, validity assessment, source access and policy/cutoff checks remain in force.

Source evidence: `outputs/research-followup-qa-20260924/qa_a04_recovery_final_producer_readonly_20260925.md`.

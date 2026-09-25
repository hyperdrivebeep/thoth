# Post-execution learning and completion contracts

2026-09-14 C05–C08 repair. These extend existing owners; they add no new memory store,
scientific evaluator, risk authority or external execution permission.

R2 completion exposes a typed CommittedExecutionBasis with the committed Outcome revision,
updated research revisions, producer-owned TestValidity/PredictionFit and observation refs.
After the effect checkpoint, PostExecutionMemory captures a fresh Memory preparation basis
and uses the existing prepare/review/commit pipeline. DOMAIN_REFERENCE candidates retain exact
owner revisions. Memory transitions and PostExecutionLearningResult commit in one ledger UoW.

The learning record belongs to the Thread revision family, codec 2.1.0. Existing 2.0.0 records
retain their bytes and decoder. Exact basis digest prevents differing replay results. Existing
thread/resume retries pending/held learning after successful execution without re-running the
sandbox. thread/read exposes current learning separately from the immutable operation result.
Review/commit failure does not authorize repeating the external effect.

Learning-only resume reuses the existing Thread lease. Concurrent resumes report
LEARNING_ALREADY_RUNNING; only the lease owner can publish. Failure checkpoints use an exact
pending-head comparison and cannot overwrite another worker's completed learning checkpoint.

Memory COMMIT/HOLD/QUARANTINE remain individual reviewed outcomes. An Outcome process record
can be committed while related hypothesis/action memories remain HELD by existing conflict
review. INVALID/POST_HOC does not become hypothesis confirmation or official attainment.
Next-request recall checks project, scope, current owner, source authority, cutoff and relevance.

Default Memory review remains deterministic. An explicitly configured OAuth Memory reviewer
requires a bounded executor inside research and consumes frozen model/effort, final-wire
reservation, remaining research deadline and usage observation. Its small verdict schema
reserves 512 output allowance units, converted by the transport to the existing local byte
ceiling. Unbounded legacy CLI review is not dispatched inside a research budget; its historical
non-research API remains available. No live Memory-model claim follows from deterministic tests.

improvement_observation distinguishes NOT_TRIGGERED (NO_EXECUTION/NO_FAILURE/no qualifying
event), OBSERVED below the unchanged threshold, and actual EVALUATED/HELD results. Failure
observations bind request and execution attempt and are deduplicated. Existing paired evaluator,
frozen scorer, candidate/exposure and rollback contracts remain authoritative. A key or process
completion is not evidence of scientific improvement.

Closure contract_version 2 open items require item_id, disposition, owner or verified followup,
trigger_or_due and residual_risk (description, KNOWN/UNKNOWN, basis_refs). Missing v2 structure
is INVALID_PARAMS without a readiness write. Legacy incomplete items remain NOT_READY with
field-specific reasons. UNKNOWN risk remains UNKNOWN; no ACCEPTED_RISK/waiver permission is
added. Decisions recheck readiness; export/reopen retain detail. Historical CLOSED bytes remain.


Terminal-before-seal crash recovery also supports a RUNNING operation through thread/resume or
same-key replay, but only for RETURNED effects with a current committed Outcome and a matching
learning checkpoint. Existing TestValidity lifecycle validation checks producer seals/execution
binding; the owner/request/policy/source checks and persistent budget remain in force. Unknown
DISPATCHING/RETURNED effects without that proof remain reconciliation-only. The recovered original
operation receives an explicit POST_EXECUTION_RECOVERED_PARTIAL terminal manifest: external work
is not repeated. Later improvement/baseline work is named as not verified in this recovery;
without durable evidence this does not assert whether a later pre-crash stage ran.
Only explicit thread/resume can clear a learning pause, under the exact owner, verified committed
basis, current lease and transactional Thread CAS. Plain replay retains PAUSED/PAUSE_PENDING
without failing the original operation. Pause, cancellation and lease loss during recovery stay
pending/fenced and never become a generic failure of the already executed operation.

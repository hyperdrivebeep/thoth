# V2 running input queue — local source contract (2026-09-24)

Status: LOCAL_IMPLEMENTED / FOCUSED_VERIFIED / FINAL_HOSTED_QA_PENDING / PRODUCTION_FROZEN

## Admission and identity

`thread/input` with `contract_version=2`, a new operation distinct from the current
request's operation, and a RUNNING predecessor records a
`QueuedResearchInput` control record at
`RESEARCH_EXECUTION / queue:<operation_id>`. The existing operation claim owns the
idempotency key. The typed record stores the submitted text and edit kind, input ID,
project/thread/operation IDs, strict ordinal and predecessor operation, actor/session
and data scopes, accepted request-head digest, policy/cutoff/workstream/source-binding
basis, and resolved model settings. The original model-selection input is retained so
activation can compare the same `source_by_field` and settings digest.

Admission returns `QUEUED_AFTER_CURRENT`, the durable queue ID, and
`request_ref: null`. It does not create a request revision, research attempt, input
delivery, or model call and does not move the current request head. This is a
nonterminal RUNNING operation. The old `ThreadInputRecord` text queue remains a
legacy path and never consumes this v2 record.

### Existing-operation reentry (2026-09-25 correction, acceptance pending)

A same-key replay that already has a durable `ResearchAttempt` must pass current
execution-owner authorization and reenter that exact attempt before considering new
queue admission. It does not author another input, request revision, attempt, queue
ordinal or budget. Existing attempt replay retains request fences, lease ownership,
pause/stop and external-effect reconciliation. An uncertain DISPATCHING effect stays
`EXTERNAL_EFFECT_RECONCILIATION_REQUIRED`; reentry never repeats that effect or marks
the original operation failed merely because automatic execution is not allowed.

Every new queue edge must point to a different operation. Replaying the current
operation while a later input is already queued must not append the current operation
behind that input, since this would create a cycle. A matching current request with a
missing durable attempt is an inconsistent admission state and must return a bounded
pending/HOLD result without fabricating a fresh request or waiting on itself.

Previously recorded self-dependencies must remain readable and produce an explicit
bounded HOLD projection rather than an infinite wait or automatic activation. The
original record and operation/attempt/budget are preserved. An explicitly authorized
same-key request with a valid original attempt may follow that original attempt's
existing replay/reconciliation path. This correction does not authorize rewriting or
deleting stored user queue records, resetting budgets, or restarting provider work.

## Activation and disposition

Within one SQLite `BEGIN IMMEDIATE` write unit, the queue checks its latest state,
predecessor terminal operation and result, exact predecessor request head, current
project/thread/actor authority, cutoff, policy, source bindings, and model settings.
It then publishes the authored input, next request revision, research attempt,
`InputDelivery`, and `QUEUED→ACTIVE` queue record. A second local runtime using the
same SQLite workspace sees the changed queue state and cannot publish a second
request/attempt. The normal research lease and request fence still guard model work
and late result publication.

Two queued inputs point to their immediate predecessor operation and activate in
ordinal order. A queued `REPLACE` whose expected request epoch changed becomes HOLD.
`thread/steer` uses its immediate request-head transition and marks waiting inputs
SUPERSEDED. Failed/cancelled predecessors, project closure, source/authority/basis
drift, and unavailable terminal results hold the queued input. The held record
retains its reason; it does not trigger a model call. A later explicit steer can
supersede a held input so new work is not permanently blocked.

On shutdown a QUEUED operation stays durable. Hosted stale recovery preserves
QUEUED/HOLD operations but seals an ACTIVE operation whose remote effect is
uncertain. Reentry uses the same idempotency key and caller authority. A predecessor
sealed FAILED after restart makes the queued input HOLD; no automatic model rerun
occurs. The supported hosted execution configuration has one active runtime per
session workspace. A second hosted runtime pointing at that same workspace is
not rejected at entry today. Its startup stale sweep may seal the first
runtime's ACTIVE operation, so hosted cross-runtime end-to-end completion and
original-operation preservation remain NOT_RUN. The separate two-runtime local
SQLite test proves one activation commit, not safe hosted multi-runtime
ownership. Hosting must keep one runtime per session workspace until an
explicit distributed-owner guard is implemented.

`operation/cancel` seals its target in the operation store first and then records
`QUEUE_OPERATION_CANCELLED` for a queued target. These two stores do not share one
transaction. A crash between them can leave the queue record at QUEUED while the
operation is already CANCELLED; the operation state is authoritative and prevents
activation or replay. Runtime startup reconciles such a terminal operation to a
typed HOLD record without running a model. This is separate from queue admission
and activation atomicity.

## Storage and verification boundary

The existing versioned control-record ledger stores the queue; no SQL schema
migration or backfill is needed. The `QueuedResearchInput` model and adapter validate
record kind, version and project/operation identity on reads. New protocol payloads
are additive for v2; authored-request-free legacy `thread/read` keeps its early
return. `thread/read.queued_inputs_v2` projects queue ID, order and disposition
without executing queued work.

Focused synthetic model tests cover head preservation, sequential application,
same-key replay, local two-runtime activation serialization, restart HOLD,
predecessor failure, queued cancel and startup reconciliation, stale REPLACE, and steer fencing. A separate
single-session normal hosted HTTP test covers admission and first-result preservation.
Q's six-scenario HTTP acceptance and B's hosted stale-sweep read recovery must be
judged on the final combined source before integrated PASS. No model provider,
production runtime, deployment, or GitHub publication is part of this local work.

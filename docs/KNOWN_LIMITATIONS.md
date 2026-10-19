# Known limitations

THOTH is an experimental source preview. This page lists situations where a saved result or its
activity log can end up incomplete. **None of them affects normal use.** They appear only when
a storage error or an unexpected shutdown happens at a specific moment, or when THOTH is used in a
way it does not support. Each entry says what you see and what you can do. The overall
verification state is in [VERIFICATION.md](VERIFICATION.md).

한국어 안내: [KNOWN_LIMITATIONS.ko.md](KNOWN_LIMITATIONS.ko.md)

## Local access trusts the user of this computer

Local mode assumes one user and trusts programs on the same computer without authenticating them. The server checks local Host names and, for requests that can change state, accepts only local HTTP Origins when an Origin is present; RPC and cancellation requests also require JSON. This rejects requests with foreign Host/Origin values and plain-text RPC bodies while preserving the built-in UI, the Vite development proxy and local JSON clients. These checks are not authentication: another program on this computer can omit Origin and send allowed headers. Hosted review has separate access controls.

## One THOTH process per workspace is enforced

A workspace is open in one THOTH process at a time. Starting a second one on the same workspace
stops with exit code 3 and a Korean and English message that names the other process, and a server
that is already running answers a request with a "workspace in use" error (HTTP 503) instead of
writing. Close the other one and run it again. The check relies on the operating system's file lock
on a local disk. It does not cover a workspace folder shared between Windows and WSL, or one that a
cloud sync client or a network share exposes to several computers; do not open the same workspace
from two places that way. The hosted review mode does not use this lock.

## Recovery of a request interrupted by shutdown

In LOCAL mode, 67 measured commands can be closed as failed when the same idempotency key is
sent again and the original worker is confirmed gone. THOTH also requires matching host/boot
identity and an already-held workspace lock. It returns `OPERATION_OWNER_GONE` with guidance to
check what was saved; it does not rerun the handler or scan old requests automatically.

Requests outside that list (including research start/input/steer and pause/resume/stop), old
records without ownership, an unavailable or different host identity, and a live or unknown
worker can remain running. A timeout alone is not proof of death. The hosted review recovery
policy is unchanged. `improvement/evaluation/run` remains outside the list pending separate
review because its no-effect response lacks an explicit unchanged marker.

If sealing wins a race with the original completion, a domain change may already be saved while
its ordinary change notification is omitted. Check the saved result before sending a new request.
A new question may use model capacity again; a newly refreshed revision guard can also permit a
second change. This recovery is not a guarantee of exactly-once external effects.

## A finished request can miss some follow-up log entries

When a request finishes, its result is saved first. The checkpoint, notification and "succeeded"
entries of the activity log are saved afterwards. If a storage error or shutdown hits between the
two, the result and its stored links stay intact and the same request returns that result, but
those follow-up entries can be missing and are not filled in later. You do not need to repeat the
request.

## Sandboxed research tests can leave a partial record

Some research tests run in an isolated sandbox. Running it outside the database cannot be undone,
and a few steps around it are saved separately:

- If the workspace changes while a test is running, THOTH discards the result. The result is
  marked as not usable from the moment it is stored, so it is never used as new evidence. If the
  application stops between the two saves that follow, one audit entry saying the result was held
  can be missing.
- If a test fails and the application stops after the failed attempt is closed but before the
  suggested recovery is saved, the attempt is recorded as failed and the suggestion can be missing.
  Asking again normally creates it.
- If the test has already run and saving its result then fails, the run has still happened. THOTH
  does not run it again on its own.

## A research answer can be saved while the reference step fails

For a question that also needs a reference-calculation step, the research answer is saved first and
the reference step is saved separately. If the reference step fails, you see an error but the
research answer stays saved. Ask the reference part again.

## Memory proposals write their control record in two steps

Validating, proposing a change set for, or retiring a memory entry writes its control record in two
saves. After a storage error the second record can be missing. Leftover records are not read by
promotion or selection, so nothing wrong is used. Repeat the proposal if you need it.

## Approvals, attempts, effects and reconciliations are linked rows, not independent revisions

Authorization envelopes, step attempts, effects, reconciliations and the outcome child records
(attribution, change set, impact) are stored as append-only rows in their own tables. Each is linked
to its parent plan or execution revision, and most changes also write an audit entry. They do not
have a revision history, current head or receipt of their own. The impact step does not write an
audit entry.

What this means in practice:

- The latest row of an approval is read as its current state. A single-use approval is consumed
  inside one write transaction, so two requests cannot both use it. The rule rests on that
  transaction and on a check of the latest revision, not on a uniqueness constraint.
- A restore creates a new revision. It does not renew a used or expired approval; approve again
  if you need one. This is by design and has not yet been covered by an end-to-end round-trip test.
- An export package does not promise to contain every approval or attempt row. A backup of the
  whole workspace database does keep them.
- Receipts are local records. They are not signed and use the local clock.
- An effect that already happened outside THOTH cannot be undone by THOTH. A record of it does not
  prove what the outside system did.

## Partial verification of atomicity

Ten record areas (project governance, sources, evidence, criteria, decision objects, hypotheses,
actions, execution and outcome, improvement, and closure/export) are listed as partially verified;
the verification page shows them as open debts. Focused tests cover the normal saving paths and many
failure points for most of them, and they are listed there as scoped evidence, not as complete
proof. The entries above are the known gaps that were found while doing this work. Other gaps may
exist.

## Smaller items

- A recovery sweep that reports zero closed requests does not say whether nothing needed closing or
  a storage error was caught.
- A failed evaluation of a self-improvement candidate can be closed as failed, and a candidate with
  the same content is then not evaluated again in the legacy configuration without an evaluation
  catalog.
- A step whose last attempt ended with a terminal failure is not dispatched again on its own.
  The execution waits until you reconcile it or create a new revision.
- File upload has no time limit of its own in the interface, only the server's size limit.

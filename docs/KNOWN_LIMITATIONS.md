# Known limitations

THOTH is an experimental source preview. This page lists situations where a saved result or its
activity log can end up incomplete. **None of them affects normal use.** They appear only when
a storage error or an unexpected shutdown happens at a specific moment, or when THOTH is used in a
way it does not support. Each entry says what you see and what you can do. The overall
verification state is in [VERIFICATION.md](VERIFICATION.md).

한국어 안내: [KNOWN_LIMITATIONS.ko.md](KNOWN_LIMITATIONS.ko.md)

## Use one THOTH process per workspace

A workspace is supported with one running THOTH process. Starting a second one on the same
workspace is not blocked, and two processes writing at the same time can leave two records with
the same version number. A later read may then show the older one. If you are unsure whether
another instance is running, close it before you continue.

## A request interrupted by a shutdown can stay "running"

THOTH records a request as running before it starts the work. If the application stops while the
work is in progress, the record can stay "running". Sending the same request again with the same
idempotency key returns "running" instead of starting the work a second time, so that nothing is
done twice. To continue, ask again as a new question. A new question can use model capacity again,
so check first whether the earlier result has already been saved.

In the hosted review mode a restart can also close such a request with a message that the previous
call could not be confirmed. See [VERIFICATION.md](VERIFICATION.md) for that mode's known limit.

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

# Canonical owner matrix

The machine-readable source is `config/architecture-conformance.json`. This page explains the rule.

| Aggregate family | Canonical owner | Current/read projection | Audit/receipt |
|---|---|---|---|
| Project, policy, roles, resource scope | project/governance revision, typed ResourceScopeRecord history | project/governance tables and resource_scope_heads | event + transition receipt |
| Source, artifact, span | artifact/source ledger | structural/evidence indexes | acquisition event/receipt |
| Evidence/Observation/Claim | evidence revision ledger | evidence graph tables | evidence audit + transition receipt |
| Criterion | criterion revision ledger | criterion table | criterion audit + transition receipt |
| Decision Object | semantic revision ledger | object table | object audit + transition receipt |
| Hypothesis | semantic revision ledger | hypothesis tables | hypothesis audit + transition receipt |
| Action/Plan | semantic revision ledger | action tables | action audit + transition receipt |
| Authorization | append-only rows linked to the plan revision (see below) | authorization table | action audit |
| Execution/Outcome | execution/outcome revision ledger for the execution and outcome records | execution/outcome tables | execution/outcome receipt |
| Attempt/Effect/Reconciliation, outcome child records | append-only rows linked to the parent execution or outcome revision (see below) | their own tables | execution/outcome audit |
| Memory | memory revision ledger | memory/context projections | memory receipt |
| Improvement | improvement revision ledger | evaluation/exposure projections | improvement receipt |
| Closure/Export | closure/export revision ledger | package/manifest projections | closure/export receipt |

Specialized projection writes and canonical commits must share a Unit of Work. Remaining partial
owners are acceptance-bound atomicity debts; they are not permitted architecture exceptions.

Authorization envelopes, step attempts, effects, reconciliations and the outcome child records
(attribution, change set, impact) are not ledger projections. Each is stored as append-only rows in
its own table and linked to the parent plan, execution or outcome revision; a change appends a new
row that names the previous one, and most changes also write an audit event. These rows have no
semantic revision, head or receipt of their own, and the impact step writes no audit event. The
parent record carries only references (for example attempt, effect and reconciliation IDs), not the
child state. The single-use rule for an authorization is enforced inside one write transaction by
reading the latest row and checking its expected revision; no uniqueness constraint backs it. A
restore does not copy an approval into a new grant. The `projections` lists in
`config/architecture-conformance.json` name these tables too; for them, read "projection" as a
specialized table, not a view derived from the ledger. Aligning that file's wording is a separate
change. Whether these records should become ledger aggregates, and which inspection guarantees they
need, is open; see [KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md).

N05 measured run subrecords are typed `PairedRunRecord` values owned by `SqliteEvaluationRunStore`
in `evaluation_runs`. The indexed plan/state/revision/hash columns are checked projections of the
sealed run content. Reservation and per-arm CAS commits bracket I/O; final scoring commit rollback
preserves already durable arm receipts. Immutable output blobs are not canonical until referenced.
This producer boundary does not close the wider IMPROVEMENT owner debt for public lifecycle,
exposure, promotion and rollback; owner-wide closure remains N07.

D02 source ownership is a governance policy record, separate from source bytes and research truth.
Its immutable scope history, current head and non-truth transition receipt share the source intake
UoW or the explicit grant/revoke UoW. A missing legacy scope is UNKNOWN and is never defaulted to
PROJECT_SHARED. Parent access is intersected at the next use; old receipts do not authorize access.
This scoped addition does not close the full PROJECT_GOVERNANCE or SOURCE_ARTIFACT_SPAN debts.

Operation result resource bindings belong to the protocol journal, not to canonical research data.
New result/error bindings seal the operation/project/request/output digests with the actual resource
uses in the same terminal journal transaction. A legacy missing binding remains UNKNOWN; cached
operation reads and replay must recheck current resource permissions, not trust old successful output.

# Mutable governance history, schema 1.0.0

`project_policies` and `project_references` are already append-only records. Resource scope has
its existing immutable history/head/receipt. These owners are reused rather than copied into a
generic semantic ledger. Only mutable Project, Role and SourceBinding projections receive the
new `governance_revisions` owner and `governance_heads` selector.

Each revision seals its record kind/ID/project, sequential revision, predecessor, exact stored
projection, related governance heads, actor attribution and observation time. Its separately
digested receipt binds the same identity, predecessor and after digest. The projection snapshot
preserves the version-one stored representation; it is not a new policy interpretation. Authority
and execution decisions remain in the existing application services.

SQLite writers stage the revision and receipt on the caller's existing connection and CAS the
head in that same transaction. ProjectStore create/update, GovernanceStore role/binding writers
and the Acquisition UoW's direct Project/binding writers all use this common writer. Existing
Project/Role/SourceBinding reads validate the projection against its current immutable owner.
History reads verify every receipt and predecessor and must terminate at the current head.

Project policy/role/reference changes continue to publish within their existing application UoW.
ProjectPack bootstrap now commits Project and its policy together; parser construction and actual
source ingestion remain outside that bootstrap transaction. Acquisition reservations, returned
effects, cleanup and UNKNOWN observations retain their separate phase contracts.

## Policy content and publication identity

2026-09-25 repair contract (implementation and regression acceptance pending): an authorized
`project/policy/update` with a fresh idempotency key and current `expected_revision` publishes
one new immutable policy version and one Project revision. This preserves the existing update
path's historical-event semantics even when the effective payload matches the current policy.
Replaying the same key/input returns its original result without publishing another version or
another update notification. A stale expected revision rejects before publication even if its
payload happens to match the current policy.

`policy_digest` remains the content digest of Project ID and merged policy payload. It does not
include policy version, ID or creation time, and old digests must not be recomputed. Consequently
different historical versions may legitimately have the same content digest, including A→B→A.
`policy_id` and the unique `(project_id, version)` pair identify publications; the highest version
and the Project's `policy_binding_ref` must agree. The digest column must not enforce uniqueness
across historical publications. A forward schema migration must preserve every existing row,
payload representation, ID, digest, timestamp and governance receipt/head. This contract does
not authorize running that migration against user or production databases.

Downstream authorization continues to bind policy ID, version and digest together. Reusing old
content must not resurrect an old policy authorization. Policy, Project, history and receipt
publication remain one transaction; same-revision concurrent writers have one winner and no
orphan policy for the loser. The successful publication emits one `project/policy/updated`
notification. It describes a recorded policy publication, not a claim that content changed.
After A→B→A, old A's digest equals current A's digest, but its ID/version is stale: the denial
is `POLICY_BINDING_MISMATCH`. `POLICY_DIGEST_MISMATCH` remains the denial for genuinely
different content. Both preserve the authoritative policy identity in the denial receipt and
reject before connector I/O or canonical mutation.

Migration `19f4c2e65b70` adds the history tables and records populated old rows as one observed
`LEGACY_BASELINE` each. It preserves all old rows, IDs, timestamps, policy digests and rights.
It records the migration observation time and `UNKNOWN_LEGACY` actor, with no predecessor; it
does not invent overwritten past states or their authors/times. New changes chain from this
baseline. Reapplying the migration head does not append another baseline.

The revision's source of authority is the existing local/authenticated operation context. A hash
or local actor label is not institutional identity assurance. This change adds no public method,
request/response field, privilege, model route or UI contract. Owner-wide atomicity remains OPEN
until the path ledger and final verification establish its complete required coverage.

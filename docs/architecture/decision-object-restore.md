# Typed Decision Object restore

The active `thread/start` producer materializes `DecisionObjectRecord` with schema version
`1.0.0` and writes its bytes as an `ENTITY_SNAPSHOT` under the `DECISION_OBJECT:<object_id>`
head. The snapshot's `object_id` and `project_id` must match its semantic revision; only the
producer's `ENTITY_SNAPSHOT` content digest is accepted for this profile. The older, smaller
`DecisionObject` shape
(`title`, `problem`, `object_profile`) is a different codec and is not advertised by the new
`decision-object-record.v1` restore profile.

The profile requires an explicit embedded `1.0.0` schema version. It keeps the existing
six typed families unchanged and adds `trigger_evidence_refs` to the evidence basis checked
by the normal restore planner. A parent object, requirement, cutoff or relation reference
without a proven current binding remains `RESTORE_MEMBERS_REQUIRE_REVIEW`; empty/missing or
revoked evidence fails the existing source-basis checks. Descriptive focus/workstream tags
are not treated as canonical member revision IDs. Public A07 proposal/merge revisions retain
the producer's content `revision_digest` inside the immutable snapshot even when the outer
semantic revision digest changes; restore identifies the target by the outer revision and
checks the snapshot's independent content digest rather than assuming those two digests are
always equal.

Normal `revision/restore` and v2 `revision/restore/preview`/`apply` use the same registered
profile, current-head CAS, authorization, source/reference checks, transaction, and impact
planner. A restore publishes a new child revision and preserves the historical snapshot.
Dependent recalculation requires a **current** downstream head and an edge bound to a current
endpoint revision. An old synthetic A07 edge pointed at a nonexistent hypothesis head and
had a relation digest unrelated to either current endpoint, so it could not legitimately
prove recalculation. The corrected A07 fixture uses a second current Decision Object and an
edge bound to the source's current revision; a stale edge remains excluded. A fault after
publication participants rolls back the new head and dependent state.

The resulting contract is synthetic/local D4 evidence. It does not restore external effects,
relax resource scope or authority, migrate stored schemas, or certify all historical
Decision Object variants. Source-frozen FULL and the public manifest remain separate root/Q
work.

# Retrieval lanes

One pure function, `select_retrieval_context` in `thoth.domain.retrieval_lane`, chooses the candidate
shortlist for a research request. The research request, the replay of a policy shadow and the
evaluation of a retrieval policy all call it, so a policy is measured with the rule that serves
requests. This is a selection of candidate spans. It is not a claim that unselected source was
searched, and it does not check meaning, absence or causality.

## Frame and lane

The function has a fixed frame and one replaceable part, the lane.

| Owner | What it decides |
|---|---|
| Frame (`select_retrieval_context`) | Which spans may be read at all (`connected_retrieval_spans`: scope, binding, cutoff, authority). Whether the whole eligible catalog is used. Pinned spans. The span and character budget. |
| Lane (a registered ranking rule) | The order of the eligible spans. A lane only ranks. |

The frame works in this order:

1. The eligible spans are fixed first. Nothing a lane or a policy says can widen them.
2. If the structure observation covers every eligible span and the catalog fits the policy's span
   count, structure count and character budget, the whole catalog is returned in document order
   (`FULL_ELIGIBLE_EXTRACTED_CATALOG`). The observed ordered IDs must equal the eligible IDs exactly;
   otherwise the ranked path is used and the limitation `CONTEXT_ORDERED_REFS_MISMATCH` is recorded.
3. Otherwise pinned spans come first, only when they are eligible and up to `max_spans`. A pin that is
   not eligible is ignored; it never makes a span readable.
4. The lane's ranking is then walked in order. A span that would exceed the span count or character
   budget is skipped and the walk continues. The mode is `LEXICAL` when every source has a complete
   structure observation and `LEGACY` otherwise.

### Registry

Lanes live in the registry `LANES` (lane id to ranking function). Adding a lane means registering it;
`select_retrieval_context` is not edited. A lane id that is not registered is refused with
`RETRIEVAL_LANE_UNKNOWN`.

One lane exists: `LEXICAL_SUBSTRING_V1`. It is the rule the research path has always used:

- Search terms are the question's and the planner's queries' tokens of at least three characters,
  plus whole two-syllable Korean words that are not on a short function-word list. Particles are not
  split off a token, so a token with a particle attached matches only as written.
- Short lines that are page chrome are dropped before ranking unless their structure protects them.
- Order: a span whose ID or artifact ID is written in the question first, then more matched terms,
  then longer text, then a fixed positional tie-break. Spans are interleaved across artifacts.
- When any span matches, only matching spans are ranked.

## Policies 2.0.0 and 3.0.0

| Policy | Class | Fields |
|---|---|---|
| 2.0.0 | `RetrievalBehaviorPolicy` | `max_spans` (at most 160), `character_budget` (at most 50000) |
| 3.0.0 | `RetrievalLanePolicy` | the same two budgets plus `lanes` |

- 2.0.0 is unchanged and always uses `LEXICAL_SUBSTRING_V1`. Adding a field to it would change the
  serialization, and so the digest, of every stored 2.0.0 policy, which is why 3.0.0 is a separate class.
- `lanes` holds exactly one entry for now, and its only allowed value is `LEXICAL_SUBSTRING_V1`
  version 1.0.0. With that default, 3.0.0 selects exactly what 2.0.0 selects for the same budgets.
  Allowing more lanes later widens the policy, the registry and its checks together.
- The behavior policy union is discriminated by the pair (`kind`, `version`). A stored policy without
  a `version` is read as 2.0.0. An unknown version is invalid and compiles to
  `BEHAVIOR_POLICY_INVALID`.
- The baseline stays 2.0.0. A 3.0.0 policy reaches a run only through the existing exposure flow
  (shadow, then approved canary), never by default.

## Shadow replay

When a retrieval-policy shadow is armed for a research request, the candidate stage hands the same
retrieval request to `select_retrieval_context` again with the shadow's own policy, once per shadow.

- The shadow's result is journaled as an `EVIDENCE_SELECTION` use against the shadow snapshot, as if
  that policy had been active: the input digest is the same, and the output digest and the stage label
  come from the shadow's own result.
- The result never reaches the request. The order and content of what the models receive are exactly
  what the active policy chose; a shadow has no model calls (its observation records zero).
- The adjacent-packet stage has no shared selection function and differs only by budget, so it is
  replayed by running the stage again under each shadow.
- Shadows of other components do not take part. A 2.0.0 or 3.0.0 retrieval shadow is observed
  successfully only when at least one use was journaled against it and none was held.
- The older analysis path keeps its own scoring function and is not part of this change.

## Evaluating a retrieval policy

A paired evaluation of a retrieval policy runs the policy's evaluator in the isolated worker. Both
policy versions delegate to one function, which builds a retrieval request from the case and calls
`select_retrieval_context`.

- Case payload: `problem`, `evidence`, and optionally `queries`, `pinned_refs` and `catalog`.
  `forced_refs` keeps its old name and means the same as `pinned_refs`. A pin that is not eligible is
  ignored, as in the research path; the earlier evaluator treated it as an error.
- Output keys: `selected_refs`, `excluded_count`, `selected_characters`, `mode` and `evaluator`.
- The evaluator is `retrieval-lane-dispatch:1.0.0`. It is written into every output, which the
  evaluation receipt hashes.

### Identity and mixing

- For retrieval pairs only, the evaluator id is added to the input of `dedupe_digest`. It is not a
  field of the evaluation spec, because one more spec field would invalidate every stored spec. The
  digest of every other component is unchanged.
- A result made by another algorithm is therefore never returned for a new request, and an older
  retrieval pair is read under its older identity.
- When an interrupted pair is resumed and an arm that already exists was made by another evaluator
  (or records none), the pair stops as held with `EVALUATION_ALGORITHM_CHANGED` and the algorithms
  are not mixed. A retrieval pair left unfinished before the evaluator id existed is held this way; a
  new submission gets a new identity and runs normally.

### Operating note: reuse limit

An evaluation bundle (identified by its `fixture_digest`) allows a limited number of pairs,
`max_pair_runs`, which defaults to 1. When that many pairs already exist, a new pair on the same
bundle is refused with `EVALUATION_EXPOSURE_REUSE_LIMIT`. A bundle that was evaluated with an older
algorithm therefore cannot be evaluated again with the current one unless the limit is raised for that
bundle or the bundle gets a new version. Stored pairs are never re-run; reading one only verifies the
hash of its stored output.

## Not part of this contract

- No lane other than `LEXICAL_SUBSTRING_V1` exists. A lane for protected identifiers, character
  bigrams or grouped scoring would register in `LANES`, widen `lanes` and the policy checks, and bring
  its own evaluation evidence.
- The reranking model, the adjacent-packet expansion and the context assembly are separate stages
  that consume the shortlist (see [positional evidence context](evidence-context.md)).
- Nothing here changes an authority, cutoff or access check, a stored policy, or the 2.0.0 baseline.

## Tests

The research selection is pinned by an equivalence suite against a preserved copy of the earlier
rules, with a digest over every scenario. Shadow behavior is pinned by `test_retrieval_shadow_replay`
(a shadow never changes the order, and its journal equals the one the same policy writes when
active) and `test_work_candidates_record` (the recorded selection follows the selection rules).
`test_retrieval_shadow_normal_entry` runs the whole chain through the normal request entry: an
evaluation pair, an exposure that rests on it, a research request, and the replayed shadow.
`test_retrieval_evaluation_pair` covers the evaluator id, the pair identity and the held resume.

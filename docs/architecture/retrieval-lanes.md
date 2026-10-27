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

Lanes live in the registry `LANES` (lane id to ranking function), filled by
`register_lane(lane_id, version, lane, provenance)`. Adding a lane means registering it;
`select_retrieval_context` is not edited. A lane id that is not registered is refused with
`RETRIEVAL_LANE_UNKNOWN`.

A lane is its id and its version together. A 3.0.0 policy names both, and when the registered
version of that lane differs the selection is refused with `RETRIEVAL_LANE_VERSION_UNKNOWN`, so
one policy payload can never select differently after a lane's rules change. **Any change to a
lane's rules, and for `PROTECTED_EXACT` any change to the scanner (grammars, split rule or matching
rule, versioned as `SCANNER_VERSION`), registers the lane under a new version.** A lane may declare
provenance (for `PROTECTED_EXACT`: the scanner version); a ranked selection by such a lane carries
it, and it is part of what the journaled selection is a function of and of the evaluation output.

Two lanes exist. `LEXICAL_SUBSTRING_V1` is the rule the research path has always used:

- Search terms are the question's and the planner's queries' tokens of at least three characters,
  plus whole two-syllable Korean words that are not on a short function-word list. Particles are not
  split off a token, so a token with a particle attached matches only as written.
- Short lines that are page chrome are dropped before ranking unless their structure protects them.
- Order: a span whose ID or artifact ID is written in the question first, then more matched terms,
  then longer text, then a fixed positional tie-break. Spans are interleaved across artifacts.
- When any span matches, only matching spans are ranked.

`PROTECTED_EXACT` (1.0.0) ranks by identifiers. A scanner (`thoth.domain.query_identifiers`) first fixes typed
identifiers in the question and in the planner's queries by grammar: paths, versions and decimals,
package pins, symbols and calls, environment variables, flags, references (DOI, RFC, CVE),
quantities with a unit, and letter-digit tokens. Only the text it leaves over is split, and only
where a Latin letter or digit is directly followed by a Hangul syllable (`gcc에서` gives `gcc` and
`에서`); no particle list is used and the question string is not changed. A path without an
extension followed by Hangul keeps both the written and the shorter form. In a source span an
identifier ends where a letter, digit or underscore would continue it, so a Hangul syllable after
it is a boundary, and a version does not match a longer one (1.2 is not found in 1.20 or 1.2.0).
The remaining Latin and digit pieces are matched the same way, and Hangul pieces by substring under
the two-syllable rule above. Spans with a longer identifier match come first; the lane does not apply
the short-line filter, so a short title that holds an identifier is kept, and a span with no match is
not returned, so it is meant to be combined with other lanes rather than used alone.

## Policies 2.0.0 and 3.0.0

| Policy | Class | Fields |
|---|---|---|
| 2.0.0 | `RetrievalBehaviorPolicy` | `max_spans` (at most 160), `character_budget` (at most 50000) |
| 3.0.0 | `RetrievalLanePolicy` | the same two budgets plus `lanes` |

- 2.0.0 is unchanged and always uses `LEXICAL_SUBSTRING_V1`. Adding a field to it would change the
  serialization, and so the digest, of every stored 2.0.0 policy, which is why 3.0.0 is a separate class.
- `lanes` holds exactly one entry for now. Its `lane_id` is `LEXICAL_SUBSTRING_V1` (the default) or
  `PROTECTED_EXACT`, and its `lane_version` must be the registered version, 1.0.0 for both. A stored
  policy that names the original lane keeps its serialization and digest. With that lane, 3.0.0
  selects exactly what 2.0.0 selects for the same budgets. A policy with more than one lane, and
  lane fusion, belong to a later policy version, not to 3.0.0.
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
- A shadow that cannot select (an unregistered lane, a lane version the code does not register, a
  policy that is not a retrieval policy) is journaled as `POLICY_HELD` and the request goes on with
  the active result; a shadow never fails its request. The older analysis path does the same, and an
  exposure whose shadow was held is not observed as successful.
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
- Output keys: `selected_refs`, `excluded_count`, `selected_characters`, `mode` and `evaluator`; a
  lane that declares provenance adds `lane` (lane id, lane version and, for `PROTECTED_EXACT`, the
  scanner version).
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

- Only `LEXICAL_SUBSTRING_V1` and `PROTECTED_EXACT` exist, and neither is the default: the active policy
  stays 2.0.0 and the new lane is used only through shadow replay and evaluation. A lane for character
  bigrams or grouped scoring would register in `LANES` and bring its own evaluation evidence.
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
`test_query_identifiers` and `test_protected_exact_lane` pin the scanner, the matching edges, the lane and
its version and provenance. `test_retrieval_shadow_held` and the second case of
`test_retrieval_shadow_normal_entry` pin that a shadow which cannot select is held and its request succeeds.
`test_retrieval_policy_3_0_0_digests` pins the serialization and digests of a 3.0.0 policy that names the
original lane.

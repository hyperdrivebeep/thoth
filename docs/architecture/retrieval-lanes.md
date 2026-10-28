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

Five lanes exist (`GLOSSARY_BM25`, usable in a 4.0.0 policy only, is described in its own section
below). `LEXICAL_SUBSTRING_V1` is the rule the research path has always used:

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

`GROUPED_BM25` (1.0.0) ranks structure groups, not single lines, by BM25 over the group's title and
body (see below), so a row of numbers is found through the version title, table head or heading
above it. The terms are the scanner's identifiers (every form of each) and its leftover pieces, with
Hangul by the two-syllable rule. A span that no group names is a group of its own, and a source with
no structure observation has no groups, so the lane then ranks single spans. It returns the groups in
score order, each as its title spans first and then its body in document order, skipping a span it has
already given. The frame's span and character budget are unchanged, so a budget that ends inside a
group keeps the group's titles and cuts its rows. It returns every group with a matching term, so
the budget decides how many are read.

`CHAR_BIGRAM` (1.0.0) ranks single spans by BM25 over Hangul syllable bigrams (a bigram that is a
function word is dropped) and symbol-preserving Latin and digit words, after NFKC and case folding.
It finds Korean text without a shared stem, for example a question word that carries a different
particle than the source.

### BM25

Both ranked lanes use one formula; its constants are part of the lane version, not policy fields.
`score(unit, term) = idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / average length))` with
`k1 = 1.2`, `b = 0.75` and `idf = ln(1 + (N - n + 0.5) / (n + 0.5))`, which is never negative. **N is the
number of units of the request's eligible spans (its eligible groups for `GROUPED_BM25`, its eligible
spans for `CHAR_BIGRAM`) and n the number of those units that hold the term**; spans the request may
not read take no part. Each query term counts once. Units without a matching term score 0 and are not
ranked; equal scores keep the document order of the units' first spans.

### Structure groups

A group is a title (spans that name it) and a body (the spans it holds), derived per source version
from its context observation by `thoth.domain.structure_groups` and carried in `CatalogContext.groups`.
They are never part of the source basis digest, of a stored record or of the journaled selection of
another lane. Every span of an observed source is in exactly one group's body and only once (titles
may repeat), and a group never holds spans of two sources.

- **FENCE:** one fenced code region. Each separator line inside it (`===`, `---` or `###` followed by text,
  such as `=== v1.5.5 ...`) starts a sub-group, and that line is part of the sub-group's title as well
  as its body. The title also holds the nearest heading and the paragraph that leads into the fence.
  A code comment that starts with `###` and text splits a fence in the same way.
- **LIST:** the spans of one top-level list; a top-level item that holds a nested list is a sub-group of
  its own. The title is the nearest heading.
- **SECTION:** a heading and the plain paragraphs under it; paragraphs before the first heading and
  spans no region claims form untitled sections.
- **BLOCK:** for a source with no observation, spans under the same parent pointer.
- A group is cut after 24 spans or 3000 characters, in document order, and every piece keeps the title
  of the group it was cut from. These limits and the rules are constants of the grouping version
  (`structure-groups:1.0.0`); changing one changes that version and the version of the lane using it.

Markdown pipe tables are not observed as regions (the parser reads them as paragraphs), so no group is
made for a table as such; the rules above cover a table inside a fence and a list. The parser is not
changed for this.

## Policies 2.0.0, 3.0.0 and 4.0.0

| Policy | Class | Fields |
|---|---|---|
| 2.0.0 | `RetrievalBehaviorPolicy` | `max_spans` (at most 160), `character_budget` (at most 50000) |
| 3.0.0 | `RetrievalLanePolicy` | the same two budgets plus `lanes` |
| 4.0.0 | `RetrievalFusionPolicy` | the same two budgets plus two to four `lanes`, each with a `depth` (and, for the glossary lane, a `glossary` pin), and a `fusion` method (see Fusing lanes) |

- 2.0.0 is unchanged and always uses `LEXICAL_SUBSTRING_V1`. Adding a field to it would change the
  serialization, and so the digest, of every stored 2.0.0 policy, which is why 3.0.0 is a separate class.
- `lanes` holds exactly one entry for now. Its `lane_id` is `LEXICAL_SUBSTRING_V1` (the default),
  `PROTECTED_EXACT`, `GROUPED_BM25` or `CHAR_BIGRAM`, and its `lane_version` must be the registered version, 1.0.0 for both. A stored
  policy that names the original lane keeps its serialization and digest. With that lane, 3.0.0
  selects exactly what 2.0.0 selects for the same budgets. A policy with more than one lane is
  4.0.0, not 3.0.0, which stays as it is.
- The behavior policy union is discriminated by the pair (`kind`, `version`). A stored policy without
  a `version` is read as 2.0.0. An unknown version is invalid and compiles to
  `BEHAVIOR_POLICY_INVALID`.
- The baseline stays 2.0.0. A 3.0.0 policy reaches a run only through the existing exposure flow
  (shadow, then approved canary), never by default.

## Fusing lanes (policy 4.0.0)

A 4.0.0 policy lists two to four distinct lanes. Each entry names the lane, its version and a fixed
`depth`; the order of the entries is part of the policy and is the only tie-break. `lanes` is required
and has no default: no combination is treated as a recommended one before it is measured, so the
4.0.0 handler refuses `default_policy()` (`BEHAVIOR_POLICY_INVALID`). Nothing asks it for one, because
the baseline of every request is 2.0.0.

The frame is unchanged. The eligible spans, the whole-catalog mode and the pinned spans are decided
first. A lane that is not registered, or whose registered version differs, refuses the whole
selection exactly as in 3.0.0. A `lane_id` argument passed together with a 4.0.0 policy is refused.

The merge is `ROUND_ROBIN_DEDUP` 1.0.0:

1. Each lane ranks the same eligible spans and returns **chunks**: one span for a span lane, one
   structure group (title spans, then body) for `GROUPED_BM25`. The flat ranking of `GROUPED_BM25`
   is unchanged; a 3.0.0 policy still receives it.
2. A lane keeps the first `depth` chunks of its own ranking. The depth does not depend on any other
   lane. Depth 3 on `GROUPED_BM25` means three groups; on `PROTECTED_EXACT` three spans.
3. The lanes take turns in policy order, one chunk each, rank by rank. A lane with nothing left is
   skipped. Nothing is summed, so there are no ties.
4. The merged order is admitted under the span and character budget. A span already admitted (a pin,
   or from an earlier chunk) is skipped. The first span of a chunk that does not fit ends that
   chunk: a group is admitted with its titles first and its body in document order, and never with a
   hole in the middle. The next chunk is still tried, so one large group does not keep out a small
   chunk that follows it. A chunk of one span is simply skipped, as in the single-lane frame. This
   cut rule belongs to 4.0.0; the 3.0.0 frame skips a span that does not fit and goes on.

RRF and score combination are not part of 1.0.0.

Provenance and attribution. A fused selection carries provenance with the method and version and,
per lane, `lane0`, `lane1`, ... as `id:version:depth=N` plus that lane's own rule versions
(`lane0.scanner`, `lane0.grouping`, ...). It is part of what the journaled selection is a function of.
The evaluation output of a 4.0.0 policy adds `groups` (how many groups the case carried; a grouped
lane on a case with none ranks single spans) and, when lanes ran, `lanes`: per lane its id, version,
depth, how many chunks it ranked, `credited_refs` (selected spans it gave first) and `also_refs`
(selected spans it also ranked within its depth that an earlier lane had given). Those two keys
are written only for 4.0.0, so the outputs of 2.0.0 and 3.0.0 keep their bytes and the evaluator id
stays `retrieval-lane-dispatch:1.0.0`.

## The glossary lane (`GLOSSARY_BM25`, policy 4.0.0 only)

`GLOSSARY_BM25` (1.0.0) finds, for a Korean question, the English text the other lanes cannot reach
by words. It does not rewrite the question: a matcher finds Korean technical terms in the question and
the planner's queries and gives back the English terms they stand for, and the lane ranks structure
groups by BM25 over those English terms **alone** (the question's own words are not part of its query,
so its credit can be told apart from the other lanes'). The grouping, scoring and chunk shape are those
of `GROUPED_BM25`; with no term to expand, or only ambiguous ones, the lane ranks nothing. Its provenance
is the matcher version, the grouping version, the scoring constants and the scanner version. The
ranking is registered in `retrieval_lane`; the pin check, the lane's query terms and its record live
in `thoth.domain.glossary_lane`.

Matcher (`glossary-matcher:1.0.0`, `thoth.domain.glossary`; every rule below is part of that version):

- Typed identifiers are found first by the `PROTECTED_EXACT` scanner and masked, so nothing inside a
  path, version, flag, symbol or quantity is ever expanded.
- A term is found as the longest phrase at its place, left to right, without overlaps. Spaces inside a
  Korean term match zero or more spaces (`정렬키` and `정렬 키`).
- Left edge: the text must not continue a word. Right edge: the term ends the word or is followed by
  at most two particles of a closed list (`PARTICLES`; a change of the list is a new matcher
  version). A term followed by other Hangul does not match.
- A Korean term with more than one distinct English term is ambiguous and is not expanded (it is
  recorded as abstained, reason `AMBIGUOUS`). The `domain` column plays no part. Ambiguity is judged on
  every entry of the list, whatever the confidence floor, so a floor cannot turn an ambiguous term
  into a clear one.
- A term of fewer than two syllables is never matched alone. Only Korean to English exists: an English
  term is never expanded back, and an expansion is never expanded again. An English term is used once;
  at most ten are used per request (`dropped` counts the rest).
- Not covered: verb and adjective conjugation, and terms of one syllable.

Policy. A 4.0.0 lane entry for this lane carries a required `glossary` pin, `{digest, min_confidence}`
(`HIGH` or `MEDIUM`, no default), and no other lane may carry one. The field is not written when
absent, so the serialization and digest of every existing 4.0.0 policy are unchanged; 3.0.0 does not
know the lane. The digest is a SHA-256 over every entry's `(id, ko, en, domain, confidence)` in id
order and the format string `glossary-format:1.0.0`; an entry's id is the first 16 hex digits of the
SHA-256 of `ko\x1fen\x1fdomain`. Source, licence and note columns are not part of it. A selection
whose list has another digest is refused with `RETRIEVAL_GLOSSARY_DIGEST_MISMATCH`, and one with no
list with `RETRIEVAL_GLOSSARY_UNAVAILABLE` (both `ValueError`, so a shadow is journaled as held and the
request goes on; an active policy that names one it cannot have is refused, as an active selection is
never caught). The check is made whether or not the lane would have ranked anything.

List and loader (`thoth.adapters.retrieval_glossary`). The list is a CSV in the workspace at
`retrieval-glossary/glossary.csv`, read as it is delivered: columns `ko`, `en`, `domain`,
`confidence` are required and every other column is ignored; UTF-8 with or without a byte order mark
gives the same digest; spaces are collapsed and text is NFC; the same entry twice is one entry at the
higher confidence; a row that cannot be used, a missing column, a file that is not UTF-8 or one of
more than 5000 entries refuses the whole file. There is no list by default: a missing file means none,
and the research path reads the file only when the active policy or an armed retrieval shadow names the
glossary lane (`current_glossary()`); an unreadable file is the same as a missing one. The file is
read again only when its modification time or size changes. No list is shipped in the repository.

Evaluation. A case payload (`glossary` is written only when the case has one) carries the digest of
the whole list and **only the entries whose Korean term occurs in the case's question or queries**
once spaces are left out; both translations of an ambiguous term travel together. A test pins that
this is enough to reproduce every match the whole list gives, at both floors. The evaluator checks that
each entry's id is the hash of its content and that no id is carried twice (a case that does not is
refused). The digest itself is **claimed, not recomputed**: the carried entries are a part of the list.
The evaluation output therefore records, on the lane's entry in `lanes`, a `glossary` record:
`digest`, `min_confidence`, `entries_carried`, `digest_verified` (`false` for a case), `expansions`,
`dropped`, `fired` (matched text, English term, entry ids) and `abstained`. Only this lane's entry has
the record, and only a policy that names the lane produces it, so every existing output keeps its
bytes; the evaluator id stays `retrieval-lane-dispatch:1.0.0`. A selection made by the research path
carries the verified list: its provenance (`laneN.matcher`, `laneN.glossary_digest`,
`laneN.min_confidence`, `laneN.glossary_entries`, `laneN.glossary_verified`, `laneN.fired`) says what
was used and which entries fired.


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
- Held has two reason codes. A selection that is refused (a `ValueError`) is journaled under
  `SHADOW_SELECTION_HELD`. A selection that breaks for another reason (any other `Exception`;
  cancellation is not caught) is journaled under `SHADOW_SELECTION_FAILED`. This applies to the
  candidate stage, the adjacent-packet replay and the older analysis path, and only to shadows: the
  active selection, and a lane run on its own, still raise.
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

- Case payload: `problem`, `evidence`, and optionally `queries`, `pinned_refs`, `catalog` and, for the
  glossary lane, `glossary` (see The glossary lane).
  `forced_refs` keeps its old name and means the same as `pinned_refs`. A pin that is not eligible is
  ignored, as in the research path; the earlier evaluator treated it as an error.
- `catalog.groups` (kind, title and body span ids) may be named by a case only for a comparison that uses
  `GROUPED_BM25`; when it is empty the field is not serialized, so the input, its digest and the
  comparison identity of every other comparison are unchanged. A case that names groups is refused
  when a group names a span the case does not hold (`RETRIEVAL_CASE_GROUP_SPAN_UNKNOWN`) or when a
  span is in the body of more than one group (`RETRIEVAL_CASE_GROUP_BODY_DUPLICATED`); a title
  may head several groups.
- No product code freezes evaluation cases; a case is a host-configured payload.
  `evaluation_case_payload(request)` in `thoth.domain.retrieval_case` is the one way to make it from
  the request the research path builds, groups included, and `request_of_case` is its inverse, used by
  the evaluator. Evaluating that payload selects what the request itself selects, for 2.0.0, 3.0.0
  and 4.0.0; a test pins it.
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

- None of the lanes other than `LEXICAL_SUBSTRING_V1` is a default: the active policy stays 2.0.0 and the
  new lanes and the 4.0.0 policy are used only through shadow replay and evaluation. No lane
  combination or depth is recommended here.
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
`test_structure_groups`, `test_lane_scoring` and `test_ranked_lanes` pin the group rules and their invariants, the BM25 formula
and the two ranked lanes; `test_retrieval_source_digests` and `test_retrieval_evaluation_pins` pin that the
source basis, the journal of the original lane and the evaluation inputs and outputs of the existing
policies did not move. `test_retrieval_policy_3_0_0_digests` pins the serialization and digests of a 3.0.0 policy that names the
original lane.

`test_glossary_matcher` pins the matcher rules (longest phrase, optional spaces, edges and particles,
masking, ambiguity, the one-syllable rule, direction, the cap) and that the entries a case carries
reproduce every match. `test_glossary_lane` pins the lane, the pin and its serialization, the refusal
by digest and the provenance; `test_glossary_evaluation` pins the case payload, the evaluator's
entry checks and the `glossary` record of the output; `test_retrieval_glossary_loader` pins the file
rules and the reader; `test_glossary_shadow` pins that the file is read only for a policy that names
the lane and that a shadow without its list is held; and
`test_retrieval_glossary_shadow_normal_entry` runs the chain through the normal request entry with a
workspace list.

4.0.0 is covered by `test_retrieval_fusion` (merge, depth, dedupe, budget, no hole, attribution),
`test_fused_selection` (the policy through the frame, provenance, whole catalog, pins, refusals),
`test_retrieval_fusion_policy` (serialization, digests, validation, the handler and the paths that
enumerate handlers), `test_retrieval_case` (payload equals direct selection, output keys,
groups validation), `test_retrieval_lane_flat_pins` (3.0.0 selections and outputs did not move),
`test_retrieval_shadow_failed` (a broken shadow is held, the active path is not caught),
`test_block_groups_context` (groups of an unobserved source reach the lane) and
`test_retrieval_fusion_shadow_normal_entry` (the whole chain through the normal request entry).

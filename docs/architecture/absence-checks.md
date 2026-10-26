# Declared absence claims and literal checks

`ReviewProposalE2` adds typed `absence_claims` to the unchanged `ReviewProposal` contract.
Both codecs remain registered for reading historical stages. The semantic reviewer keeps
`semantic_reviewer.v6`; its new output codec/schema and separate absence instruction change the
existing stage input/reuse basis. The v6 task sentences and the adjudicator output codec are unchanged.

## What is checked

Each claim retains its exact statement and target, target kind, declared scope, and origin in the
answer or an indexed confirmed finding. The checker accepts only
`target_language_basis=SOURCE_LITERAL`. `TRANSLATED` and `CONCEPT` declarations are UNSUPPORTED.
It never guesses a source language, translates a target, strips a suffix or searches semantically.
The model's declaration is not proof that it copied the source correctly.

Matching is case-sensitive with identifier/decimal/path boundaries. `1.2` does not match `1.20`
or `1.2.0`, and `KB` does not match `kB`. An identifier with an ambiguous attached suffix, such as
`gcc에서` or `C:\data\보고서.pdf를`, is unsupported, including when labelled LITERAL. Ordinary
Korean literals are not rejected merely because they use Korean characters. Empty or padded
targets are unsupported rather than silently repaired.
Matching is within individual recorded spans; no cross-span text is synthesized.

Checker `literal-absence:1.1.0` adds the source-side policy `hangul-source-suffix:1.0.0`.
For a target ending in a Hangul syllable, the entire immediately following Hangul run must be
empty at a non-word boundary or exactly one of these fixed suffixes:

`은 는 이 가 을 를 에 에서 에게 의 와 과 도 만 로 으로 까지 부터 보다 처럼 에는 에서는 으로는 로는 과는 와는 이나 나 이며 이고 이다 입니다 이라 이라고 라고`

A suffix prefix is insufficient: `은것` is not `은`. A preceding Hangul syllable still prevents a
match at a Hangul target start, so `보고서` does not match `원보고서`; `가` does not match `가격`.
The target is never shortened. This is a fixed source-boundary rule, not translation, morphology
or source-language inference. Targets ending outside Hangul retain the earlier identifier rules,
and the target's own ambiguous identifier-suffix rejection remains unchanged.
목록 밖 어미가 붙은 한국어 원문은 “못 찾음”이 될 수 있으며, 강한 부재 판정은 여전히 제공하지 않는다.

New check digests bind the checker version, suffix-policy version and exact suffix list, even
when the outcome is unchanged. Historical 1.0.0 envelopes retain their original digest algorithm
and remain readable alongside 1.1.0. Scope identity and the result envelope schema stay at 1.0.0;
old outcomes are not recomputed or relabelled during reading.

Learning-only recovery restores the saved scope and checked-source bindings before publishing
the recovered checkpoint. It does not start a new investigation, enlarge the original pool,
re-run matching on unchanged inputs or offer witnesses again. Unchanged assessments and delivery
receipts remain identical, including a record with no declared claims.
If a previously checked source, policy or cutoff no longer matches, the affected scope is marked
incomplete and its outcomes become NOT_CHECKED with `ABSENCE_RECOVERY_SCOPE_BASIS_CHANGED`.
The new checkpoint records a partial basis and removes only affected absence cards; the original
checkpoint, answer and unrelated cards remain intact. Request, stage, digest, committed-outcome
and authorization guards are still enforced. This fallback never authorizes repeating an external effect.

At most twelve declared claims are searched, with at most eight hundred characters in a target
or statement. Excess claims remain NOT_CHECKED with explicit reason/count. A claim must bind to
its exact stored statement before it can produce a warning. These checks do not cover undeclared
prose, entailment, causation or the scientific meaning of a string.

## Two immutable scopes

| Scope | Capture and meaning |
|---|---|
| `ACTUAL_ROLE_INPUT` (a) | Exact semantic-review request evidence, captured before dispatch and bound to the completed stage. A reused stage is labelled `REUSED_STAGE_INPUT`, not a new model call. |
| `ALLOWED_EXTRACTED_AT_REQUEST` (b) | Active, authorized connected spans admitted by the existing cutoff/authority filter at analysis start, before memory expansion. It is not a snapshot at UI acceptance. |

The allowed pool is not truncated to the model's 160-span limit. It consists only of extracted
spans; no raw-object audit or new search backend is introduced. Later source acquisition does not
silently enlarge b. Each scope records request, policy/cutoff/retrieval-policy basis, capture phase
and time, exact source/version/span/hash/locator/state members, actually checked refs, completion
and limitations. Scope digests and per-claim check digests bind these records.

Empty, missing, changed or incomplete scope inputs cannot produce NOT_FOUND_WITHIN_SCOPE.
A valid positive witness may be reported from a partial scope; the other scope remains independent.
Cancellation/authority/currentness failures at the existing execution boundary still fence the run.
Checked source spans enter the existing consumed-source basis and are rechecked at later boundaries;
spans never shown to a model are not added to the result's model-input `source_refs`.

## Results and authority

`result.absence_assessments` 1.0.0 is a typed payload in the existing result owner, beside
`checked_observations`. It adds no table, RPC, evidence revision, source hash algorithm or manifest
version. It records declared claims, stage/pointer/text binding, both scope outcomes, effective
warnings and witness delivery. Its coverage is always `DECLARED_CLAIMS_ONLY`.

- a FOUND: warn that the model received the string it claimed absent.
- a completed miss and b FOUND: warn that the allowed material contains it outside the original input.
- a unchecked/unsupported and b FOUND: report a positive b witness with a still undetermined.
- two complete misses: only “Not found within the checked scope”. There is no AUDITED_COMPLETE outcome.
- otherwise: report unsupported or not checked, never absence of the whole source.

A found witness blocks the linked confirmed-finding card and any exact duplicate statement in
that review's cards. Unrelated cards retain their existing citation/admission checks. A warning
adds PARTIAL_HOLD and separate reason codes without rewriting the original answer or granting
authority. The original proposal remains in its completed stage. Multiple reviews retain their
records; only the active review controls current warnings and display.

Publication validates the typed envelope, check and scope digests, request, original proposal
and input refs, and checked-source binding before writes in the existing result UoW. Old results
without the field remain NOT_RECORDED; no backfill is performed. FULL keeps provenance, while
SUMMARY contains only the active display rows. English/Korean UI wording follows display locale;
targets and the original answer are not translated by this checker.

## One witness offer, no new model call

An outside witness may be offered once to the already scheduled adjudicator using the existing
context assembler. The original reviewer evidence must remain byte-identical and complete;
required context, structural and span/character limits still apply. If it cannot fit, the original
input remains and the refusal is explicit. DELIVERED is recorded only after that call completes.
An interrupted prepared offer remains PENDING rather than being reported as delivered.

The adjudicator receives compact check outcomes alongside its candidate. Its original finding
citations continue to be validated against a, even if a witness is added. New witness evidence
can subsequently remain in the ordinary downstream context. Numeric observations retain their
existing records and view; new check metadata is omitted with ABSENCE_VIEW_POLICY_LIMIT if raw
quotes plus numeric/check projections cannot fit the existing character budget.

No extra model invocation, retry allowance or budget is created. Byte measurements cover final
provider serialization under fixed paired inputs and previous outputs; they are not token counts,
cost estimates or live answer-quality results.

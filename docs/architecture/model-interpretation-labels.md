# Earlier model statements handed to later roles

A later model role receives what an earlier role wrote as a labelled interpretation, not as a
confirmed fact. Stored results are unchanged; only the copy sent to a model differs.

## The view

When the request context holds reviewer `confirmed_findings` or planner `interpretation_open_items`,
the copy sent to a model replaces both fields with one `model_interpretations` view
(`schema_version` 1.0.0). It carries the fixed contract sentence:

> Items marked MODEL_INTERPRETATION were written by an earlier model role; only their cited span IDs were checked, not their truth.

Each item keeps its statement text unchanged and adds:

| Field | Reviewer finding | Planner open item |
|---|---|---|
| `label` | `MODEL_INTERPRETATION` | `MODEL_INTERPRETATION` |
| `producer_role` | `SEMANTIC_REVIEWER` | `RESEARCH_PLANNER` |
| `item_kind` | `REVIEWER_FINDING` | `PLANNER_OPEN_ITEM` |
| `check` | `CITED_SPAN_IDS_ONLY`: the cited IDs were verified to be inputs the reviewer received; the statement was not checked | `NOT_CHECKED` |
| other | `evidence_refs`, `requirement_id`, `finding_kind` | none |

With nothing to label, the context is unchanged. The view is applied to every research model call
(`ResearchModel.structured`) and to the review stage before its input basis is computed, so the
stage reuse basis reflects what the model receives. A malformed stored entry is refused, never
passed on unlabelled. The stored result keeps `confirmed_findings` and `interpretation_open_items`
exactly as before, and old results are read as before.

## Versions

`semantic_reviewer.v7` replaces v6. The reviewer may now be told to cite checked arithmetic
(see `numeric-observations.md`) and receives labelled planner items, so a stage saved under v6 is not
reused. The adjudicator stays `review_adjudicator.v6`. The hypothesis generator keeps
`hypothesis_portfolio.v5`: its schema and stored contract are unchanged, and its extra instruction
appears only in the request context when arithmetic was delivered.

## Display

The result card titles reviewer findings "AI 검토 요약(인용 위치만 확인)" / "AI review summary
(citation locations checked only)", for new and older results alike. A finding the model reports as
not found is tagged as the AI's report. No verified badge is shown.

Relevant tests: `test_model_interpretation_view`, `test_model_interpretation_labels`,
`test_model_instruction_sentences` and `ConfirmedFacts.test.tsx`.

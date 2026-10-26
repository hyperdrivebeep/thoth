# Positional evidence context

Text parser 1.2.0 preserves the existing nonblank-line leaf text, positions and identity
construction. MD, Markdown and TXT additionally carry a versioned `ContextObservation` produced
by markdown-it-py 4.2.0. YAML retains its indentation/key-path behavior and has no new observation.
The shared text parser's new ingestion metadata is version 1.2.0; old artifacts are not rewritten.
If the installed observer library version is unsupported, leaf parsing still succeeds and the
observation is UNAVAILABLE with CONTEXT_OBSERVER_VERSION_UNSUPPORTED; no regions are inferred.

The observer parses positions without rendering HTML, executing code or accessing URLs. Regions
describe ATX headings, fences, paragraphs and physical blank intervals. A paragraph immediately
preceding a fence is positional context, not a verified version label or semantic support edge.
Nested closing markers or unsupported positions retain explicit uncertainty. Coordinates use
decoded-file Unicode codepoints with UTF-8-sig decoding; source line ranges are one-based and
end-exclusive. They are not interchangeable with a PDF parser item's charspan.

## Ownership and binding

Original bytes remain in the content-addressed object store. Existing `EvidenceSpan.exact_text`
means stored extracted text, not a universal promise of byte-for-byte raw-file quotation.
Observations live in the same `StructuralDocument`/artifact-version metadata owner as the nodes.
Ingestion binds the parser's draft observation to its new source version before persistence.
Reads verify project, artifact, byte hash, version and member/range invariants. A bound observation
cannot be relabeled as another version. Unknown stored schema versions or corrupt metadata fail
explicitly; an unsupported observer library version only disables the additional observation.

Old documents without observations remain readable. A new observation is obtained by explicit
new-lineage ingestion, never by rewriting a historical source or result during a read.

## Selection and assembly

A catalog wholly represented by supported observations may use
`FULL_ELIGIBLE_EXTRACTED_CATALOG` when it fits the existing count, structure and character budgets.
This applies after current scope, active-binding, cutoff and authority checks. It means all
eligible extracted spans, not all hidden/unparsed content or the entire world.
Selection and coverage use one full-catalog admission decision. The observed ordered IDs must
match the eligible IDs exactly, without duplicates; otherwise lexical selection is used and
coverage records CONTEXT_ORDERED_REFS_MISMATCH.

Mixed or legacy catalogs retain lexical selection; supported short headings and leading
paragraphs are protected without globally removing the length/noise filter. CSV/XLSX selection
and their legacy assembly behavior are retained. Spreadsheet sheet-boundary improvements are
separate work, not implied by text-context support.

Text anchors expand to their observed heading/preamble envelope and bounded same-region
neighbors. Mandatory context is not silently represented as complete when it cannot be admitted.
Pinned text anchors whose required groups cannot fit produce an explicit hold. Optional body
omission and incomplete boundaries remain visible. Legacy table/continuation expansion is retained.

The initial planner and the reranker consume contextual input. Reranker schemas distinguish
the admitted primary candidate IDs from additional contextual quotes; contextual access does
not authorize inventing a candidate ID. `RetrievalCoverage` 1.0.0 is built from the actual input
at each model boundary and identifies catalog, eligible, candidate and input sets, omitted refs,
limitations and scope. It is not an absence/entailment/causality check.

## Projection and history

The v2 model projection sends each quote once and shares document/source-state/artifact tables.
Repeated bundle references use quote/context table indexes; outputs still cite canonical span
IDs. Canonical quote hashes, context definitions and digests remain on the server. Compaction
does not mutate `ContextPack` or discard earlier interpretations to make an answer look better.

Source-context 2.2.0 binds the context-observation digest as well as the original source basis.
Historical 2.0.0/2.1.0 hashing stays on its original algorithm. Assembly cache and stage input/reuse
bases distinguish the new projection, so old input outputs are not reused as new-context results.
Historical selected-evidence queries remain pinned to their original result/version/hash/ACL.

Claim validation, numeric calculations, semantic retrieval and interpretation reconciliation are
not supplied by this feature. Compact JSON byte measurements are not provider-token or answer-
quality guarantees. No model permissions, retry policy or research limits are raised.

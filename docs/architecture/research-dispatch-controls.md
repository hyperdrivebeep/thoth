# Research dispatch controls — 2026-09-13

This is a product runtime contract, not a development Hook setting.

Latest output policy2026-09-16: the former `output_tokens * 4` visible-byte ceiling and cumulative
SSE byte cutoff are removed from default OAuth generation. They are not actual token/account
limits. The declared output capability is now OBSERVATION_ONLY/v2; bytes and provider tokens are
reported, not converted into a generation allowance. Individual malformed/oversized SSE-frame
protection and transport liveness/cancellation remain. Earlier visible/SSE limits described below
are historical. See `../verification/output-observation-policy-20260916.md`.

Current override2026-09-16: the user removed fixed total research time/call cutoffs. Historical
900s/24-call values below describe earlier executions and are no longer enforced. The current
policy supplies observed same-thread usage to every model role and the UI, with account quota and
cached tokens UNKNOWN unless actually observed. No total model deadline is installed. Separate
SDK I/O liveness, cancellation, source/authority fences and connector payload limits remain.
See `../verification/observed-usage-policy-20260916.md` for the exact compatibility and evidence.

User decision in the existing implementation task: **동일 OAuth의 로컬 제한 경로 허용**.
The authenticated existing Codex OAuth endpoint rejected `max_output_tokens` with HTTP 400;
the sanitized receipt is `outputs/repair-u06-u09-20260913/provider/oauth-cap-result.json`.
The selected model/reasoning settings are preserved. No paid API fallback is authorized.

The allowed local OAuth route enforces bounded received stream bytes, bounded visible output
bytes, deadlines, tool-free requests and cancellation
attempts. It does **not** claim an upstream generated/billed token ceiling or confirmed remote
termination. Those observations remain UNKNOWN when the provider does not report them.
This explicit decision supersedes the server-output-cap requirement only for this local OAuth
route; project/resource/egress/authority/cutoff gates remain. The later usage-only override above
supersedes the earlier cumulative call/time limits.

Capabilities are supplied by registered transport ports and consumed through wrappers. A class
name, prompt instruction, schema validity or successful subprocess exit is not a capability proof.
Each actual dispatch reserves its final serialized payload after guidance and schema construction;
dispatch identity prevents double reservation. Repair is a distinct dispatch within the same budget.

2026-09-16 user-authorized model-input change: the model dispatch path no longer interprets
`ResearchBudget.max_prompt_bytes=180000` as model context capacity. Its numeric basis was not
established by the focused research, and the actual180702-byte request was locally blocked before
provider I/O. `reserve_dispatch` now reserves calls and legacy accounting units and records exact
payload bytes/digest independently of the generic operational byte check. Connector and other
non-model `reserve` callers still enforce that check in the same transaction as their reservation.
The historical budget field/name/codec remain readable; no migration, new allowance, byte knob or
replacement numeric cutoff is introduced. Ordinary and repair model dispatches use the same owner.

The HTTP serializer transmits the original strict, reference-constrained schema. The unsafe
generic enum compressor has been removed. This does not certify provider acceptance or increase
the900-second/24-call budget, received/visible-byte bounds, or authority/cutoff permissions.
Token-aware admission is deferred; private OAuth capacity remains UNKNOWN, not a fixed byte/token
conversion. See `../verification/model-input-admission-20260916.md` for local evidence.
The existing caller output allowance is translated into a local visible-byte ceiling at four bytes
per requested output token; that visible-byte ceiling itself is reserved. This conversion is an
engineering byte budget and makes no claim about upstream tokenizer or hidden reasoning usage.
No credential or raw rejected output is persisted in a canonical record or diagnostic receipt.

Normal-entry validation found that streaming event overhead can exceed 512 KB while visible JSON
still fits its existing output reservation. The selected local route now caps total received SSE
at 4 MiB and one call at 300 seconds inside the unchanged 900-second research budget. The
corresponding lease lasts 330 seconds. Structured receive observations record frame counts, actual
received and visible bytes, and the effective limits on success or local transport HOLD.

Actual recorded xhigh workflow: 7 dispatches, 322,432 reserved units, 810.12 seconds. The final
dispatch received 1,347,948 bytes for 15,955 visible bytes. This is a synthetic-source execution
receipt, not a latency target, independent scientific evaluation, or server-side billing cap.

Span-reference fields in semantic output schemas are constrained to provided IDs. Applicability
explanations belong in explanatory fields. Informational unexamined scope and optional research
checks do not become mandatory answer blockers; required scope, governing gates, and missing
required context remain blocking. Connected-only request scope suppresses outside discovery.

2026-09-14: scoped conflict candidates and independent-role decisions bind exact requirement
IDs, current requirement-set digest and permitted span IDs. Only affected target gates are held.
Reviewed unrelated followups remain open checks. Legacy string conflicts require reassessment;
2.0.0 snapshot bytes are preserved and only the new coverage codec is 2.1.0.

Cleanup accounting is separate from research-call reservations. Trusted connector lifecycle
code invokes a dedicated cleanup function with its parent operation/run identity and the existing
2-second allowance. It runs after research exhaustion, never admits new fetch/discovery, and
records elapsed time, return/cancel state and remote-stop UNKNOWN. Stable attempt identity
deduplicates accounting. Pending observations are reported separately as unknown, not completed
calls. Wall-clock research deadlines still include elapsed time; cleanup may finish after that
deadline using its separate allowance. No server token/cost estimate replaces UNKNOWN.


REVIEWED_CONFLICT_HISTORY is informational. A current RESOLVED/NOT_RELEVANT conflict does not
by itself require discovery. Actual requirement gaps, unresolved conflict and explicit web requests
continue to require discovery. History remains preserved in the assessment.

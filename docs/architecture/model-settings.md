# Research model settings

Status: implemented, verification recorded separately, 2026-09-13. User authorized model/effort selection in addition
to the existing U06–U09 repair work. This does not change the coding agent's model or settings.

Resolution order is an explicit request, Thread preference, project preference, then transport
bootstrap defaults (the existing Codex config for the default OAuth route). Preferences are
typed `ModelPreferenceRevision` snapshots using the existing ledger/revision owner. They are
configuration, not scientific policy or evidence. Updates require the expected prior digest;
changing a preference affects subsequent admissions, not already accepted requests.

`ThreadRequestRevision.model_settings` captures provider/model/effort, per-field provenance,
capability source and a digest at admission. `ResearchWork` passes this immutable selection to
every model role and repair; each dispatch journal stores it alongside the final wire hash.
Recovery reuses the persisted selection even if external defaults change.

Catalogs implement `ModelCatalogPort` and are registered at composition. Application logic has
no provider-name branches. The default catalog reads non-secret local Codex model metadata and
advertises its intersection with the direct transport's supported wire vocabulary. Product
orchestration tiers are not advertised as direct reasoning levels. Unsupported/unknown explicit
selections fail before model dispatch, with no automatic downgrade or model fallback.
Selection is a model route `(provider, model)`. A layer that names provider or model must
name both; otherwise resolve fails with `MODEL_ROUTE_INCOMPLETE`. Replacing the route
drops inherited effort unless that same layer names one; the new option's default_effort
is then used. Effort-only updates (`/reasoning`) keep the current route.
`ONCE_TRANSIENT_429` is attached only on the Codex OAuth path; other providers omit it.
The xAI Responses wire matches OmO native chat params plus codec json_schema: model, input,
stream, store, optional reasoning, and text.format.json_schema. Empty tools, tool_choice,
parallel_tool_calls, and instructions stay omitted. Probe 2026-09-17: that combination is HTTP 200.

Normal entry points:

- `model/settings/read`: project_id, optional thread_id; returns preference digest, selection,
  effective settings and model options.
- `model/settings/update`: same scope plus selection and expected_digest; null digest means
  create-only. An empty selection clears that scope's override.
- TUI `/model`, `/model <provider> <model> [effort]`, `/reasoning <effort>`, `/model reset`.
  `--project` updates the project default; otherwise the current Thread is used when selected.
- Web research composer model/effort selectors and explicit default save button.

The Web composer owns one selection draft. Unsaved choices apply once to the next request;
admission clears only the same draft revision in the same project/Thread epoch. Display then
derives from effective defaults. A new choice made while admission/save is pending survives
the old response. Explicit save uses its captured scope and expected digest; admitted request
settings remain immutable.

Custom legacy injected providers with no catalog retain their unchanged default route. Explicit
selection requires a catalog registration; runtime options accept a catalog without changing
application branches. Metadata is capability evidence, not proof of current account entitlement.

The Codex local cache may aggregate other providers. Namespaced router aliases are excluded from
the direct OAuth catalog. The actual selected model/effort is passed through wrappers into the
final serialized request; tests compare the captured transport body with its reservation.

## User-approved token policy change — 2026-09-14, locally implemented

Implementation record: [conversation UI and shared usage](../verification/conversation-ui-20260914.md). Shared request reservation no longer enforces the cumulative token cutoff, including old stored 400,000 settings. `thread/read.usage` version 1.0.0 reports Thread-scoped observed tokens to Web and the existing TUI progress presenter. Current transports/catalogs provide no applicable pricing/cost basis, so estimated cost is UNKNOWN without a replacement calculation or extra call. Focused checks passed; real-case QA and FULL are not claimed.

The user requested: “일단 한도는 정하지 말고 사용자에게 사용되는 토큰 보여지는게 나은듯 내가 쓰는 omo native tui 보니까 그르더라”.

- Do not enforce THOTH's own cumulative token ceiling by default. The 400,000 conservative-reservation cutoff below is the previous implementation, not the newly approved policy. Do not replace it with another arbitrary token cap or only hide the existing stop condition.
- Show actual provider-reported token usage compactly in the conversation/composer area. Label the scope (current request or current Thread), and offer input/output detail. It is usage information, not a percentage of completion, context occupancy, or an allowance purchased from the provider.
- Keep immutable dispatch/response observations. Aggregate distinct real dispatches, include actual repair calls, and do not double-count polling, replay or duplicate events. Absent usage is UNKNOWN; a partly observed total is labelled partial, not a complete measured total. Never substitute payload bytes or reserved allowance for actual tokens, or label missing usage as zero.
- Apply the approved no-token-cap policy to subsequent dispatches in both new and existing Threads without rewriting historical observations/receipts. Version any changed read contract and preserve the original stored evidence of previous limit stops.
- This specific policy change concerns the cumulative token ceiling. Provider/account limits, transport output byte controls, non-model operational input bounds, per-call deadlines, cancellation, retry/error handling, authority and transaction safety are not disabled. The separate2026-09-16 authorized change removes the model-input180000-byte cutoff (see `research-dispatch-controls.md`). Existing total-call and total-wall-clock limits are distinct and must not be silently removed, increased, or presented as token limits. If they stop work, explain the actual reason.

### User-approved display: usage tokens and estimated cost, without forced fallbacks

The user further specified: “`사용 토큰 · 추정 비용`처럼 표시 하고 제발 무리한 fallback은 넣지 말아줄래”. The compact display has these two labelled values, with the same request/Thread aggregation scope.

- Tokens come from usage already reported by actual model dispatches. Cost is an explicitly labelled estimate based only on that observed usage and an applicable, known model pricing basis, or a supplied cost observation whose meaning is known. Keep currency/basis available in detail. Subscription-equivalent estimates are not additional charges or an invoice.
- If usage or applicable pricing is absent, leave the corresponding value UNKNOWN; if only part is known, label partial coverage. Never treat missing pricing as zero, borrow another model's price, or substitute bytes/characters/reserved allowance for token usage. Verified zero is distinct from unknown.
- Do not make extra model/network calls or retries merely to fill these display fields. Do not switch model/provider/account/authentication route, scrape a billing account, introduce a fallback chain or add a general billing subsystem for this feature. Use existing response observations and available trusted metadata; otherwise display the limitation.
- Missing optional usage/cost metadata does not block an otherwise valid research result. Preserve actual execution failures and their existing handling; do not reclassify them as successful usage reads. This instruction does not remove the existing bounded handling of real model/transport errors.

The existing implementation task owns the corresponding backend usage projection and UI changes. This documentation update is not an implementation/verification claim and does not authorize a large regression or FULL run.

### Receive evidence on cancellation — 2026-09-15

Receive observations retain optional HTTP status, response ID, first-response/first-byte elapsed
milliseconds, total elapsed milliseconds and last parsed event type. Missing fields in historical
records mean UNKNOWN, not zero receive activity. Received byte counts are not token usage.

The transport carries this observation through local cancellation. The model adapter records it
through the existing dispatch usage journal and then preserves the original cancellation semantics:
an outer deadline remains a deadline; a caller cancellation remains cancellation. Neither path
claims confirmed remote termination, valid completed output or a retry. No provider fallback is added.

The receive-observation change itself did not extend the call limit. The follow-up below changes
deadline ownership, without increasing the persisted overall budget.

### Long-call deadline ownership — 2026-09-15 follow-up

Research model calls use the persisted attempt's remaining wall-clock allowance, rather than an
additional fixed 300-second ceiling. The default overall900 seconds and24 model dispatches remain.
An unscoped direct model invocation still supplies its own deadline (the existing default is300s).
The HTTP executor respects the prepared deadline. Connector-specific120-second bounds are unchanged.

`model_wait.await_current_model` rechecks the existing request/authority/source/owner boundary every
5 seconds while awaiting a model, so `ResearchLeases.validate` continues its existing sparse renewal
of the330-second lease. A superseded request, revoked authority, stopped/paused Thread or lost owner
cancels the local call; remote termination remains UNKNOWN. Cancellation drain is bounded to2 seconds;
an uncooperative task is retained only to collect its eventual exception, never to publish late output.
Timeouts do not start another call, reset the attempt budget or select a fallback model.

### Primary task profile contract — 2026-09-15 follow-up

The planner, wire schema description and compiler now agree on one justified primary task profile.
Secondary question aspects are represented by research checks. Multiple candidates still represent
genuine unresolved ambiguity and retain the compiler's HOLD; the compiler does not silently pick
the first item. The planning prompt distinguishes a retrieval shortlist from all connected material
and does not ask the planner to produce the final scientific answer or invent governing obligations.
No project/Hero names or desired answers enter product code.

Initial ranker omissions are acquisition diagnostics for the earlier shortlist. They remain recorded,
but do not independently force a final HOLD after a larger packet has been semantically reviewed.
Current reviewer/adjudicator missing scope, unsatisfied governing requirements and uncertainty still
enforce their existing gates. This does not make relevance sufficient evidence for promotion.

### Previous implementation snapshot (before the approved change)

Operational receive limits: 4 MiB total SSE bytes per call and a maximum 300 seconds, further
limited by the persisted research budget's remaining time. Visible output remains four UTF-8
bytes per requested output allowance; its complete byte ceiling is reserved. Total research
defaults remain 24 calls, 400,000 conservative reserved units and 900 wall-clock seconds.
The 330-second execution lease covers the maximum call and bounded cleanup. No server generation
or billing ceiling is implied. These replace the initial 512,000-byte/120-second transport defaults,
which stopped the recorded normal xhigh test before completion.

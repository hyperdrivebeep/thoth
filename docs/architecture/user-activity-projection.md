# User-facing research activity projection

Status: local backend contract, 2026-09-23. This document describes the additive read projection returned by normal `thread/read`; it is not a new canonical record or event journal.

## Contract

`thread/read.user_activity_events` is an ordered array of `thoth.user_activity_event.v1` objects. The generated JSON Schema is `schemas/protocol/user-activity-event-v1.schema.json`. Existing `activity_events`, `completed_stages`, model dispatch summaries, result manifests, and historical records remain unchanged.

Each event separates these two axes:

- `state` describes execution: planned, running, succeeded, failed, blocked, cancel requested, cancelled, timed out, or unknown external effect.
- `research_relation` describes the source or judgement relationship: none, discovered, opened, fetched, parsed, read, screened, selected candidate, supports, contradicts, inconclusive, held, excluded, stale, or access blocked.

A successful stage, tool, connector, sandbox, or model call therefore has `research_relation=none` unless a stored source/judgement record independently justifies another value. HTTP success, process success, and model completion do not certify evidence adoption, criterion satisfaction, or scientific truth.

`seq` is deterministic display order within the read response. `time` is emitted only when the stored record supplies one; `seq` must not be interpreted as a newly persisted event time.

## Currently projected facts

- completed research stages, with safe Korean labels, elapsed time, dispatch count, and an opaque revision alias;
- evidence-focus source reads with authorized page/cell/line locator data;
- result source counts grouped by stored support state as selected candidate, supports, contradicts, or inconclusive;
- stale and access-blocked source states when cutoff/authority records justify them;
- hypothesis total/reviewed counts;
- model wait, observation, retry, timeout, and cancellation observation, always separate from research judgement;
- operation failure, access/policy block, cancellation, partial hold, and unknown external-effect reconciliation states when the current records expose them.

The vocabulary also reserves discovered, opened, fetched, parsed, screened, excluded, connector, sandbox, and tool events. The current projection does not emit those states unless a future stored record proves them. It does not reconstruct missing raw commands, connector receipts, or sandbox traces.

## Server-side safety boundary

- Labels and explanations are server-owned copy. Source excerpts, document instructions, raw prompts, hidden reasoning, provider payloads, stdout/stderr, stack traces, and shell commands are not fields in the DTO.
- `tool.raw_command_available` is always `false`, and `tool.command_detail` is `unavailable`. The projection never fabricates a command that the runtime did not persist.
- Public HTTP targets are reduced to scheme plus public hostname. User info, path, query, fragment, signed URL material, local/UNC/Windows/Linux absolute paths, localhost, private IPs, and internal hostnames do not cross the projection.
- Page, cell, and line locators are structural values. Section text is bounded and removed when it resembles a secret, path, control-character injection, or prompt injection.
- Only evidence returned through the existing scoped artifact ledger is eligible for target enrichment. Missing or unauthorized source metadata produces a generic `project-source` target.
- `redaction.classes` explains removed classes without echoing the removed value. `source_content_included` is fixed to `false`.
- IDs exposed by the projection are one-way short aliases. Raw operation, dispatch, source-version, and revision identifiers are not required for the user view.

## Compatibility and storage

The field is additive on the v2 research-status path. Old clients may ignore it and continue consuming `activity_events` and `completed_stages`. A legacy thread without an authored request keeps its exact legacy payload and does not receive the new field. No migration, schema table, backfill, canonical event, model call, or user/project mutation is introduced.

## Verification record

Executed from the repository root with `.venv\Scripts\python.exe` on 2026-09-23:

```text
.venv\Scripts\python.exe -m pytest tests/unit/application/test_user_activity_projection.py tests/unit/application/test_research_activity_events.py tests/integration/test_user_activity_thread_read.py -q
exit 0: 11 passed

.venv\Scripts\python.exe -m pytest tests/unit/application/test_user_activity_projection.py tests/unit/application/test_research_activity_events.py tests/integration/test_user_activity_thread_read.py tests/contract/test_schema_snapshots.py tests/integration/test_stage_checkpoint_contract.py tests/integration/test_research_failure_terminal.py -q
exit 0: 21 passed

.venv\Scripts\python.exe -m pytest tests/integration/test_conversation_ui_read.py tests/integration/test_structured_context_pipeline.py tests/integration/test_thread_rpc.py -q
first exit 1: 5 passed, 1 failed because a legacy thread received an additive empty field
final exit 0: 6 passed after preserving the exact legacy payload

.venv\Scripts\python.exe -m pytest tests/unit/application/test_user_activity_projection.py tests/unit/application/test_research_activity_events.py tests/contract/test_schema_snapshots.py tests/integration/test_user_activity_thread_read.py tests/integration/test_stage_checkpoint_contract.py tests/integration/test_research_failure_terminal.py tests/integration/test_conversation_ui_read.py tests/integration/test_structured_context_pipeline.py tests/integration/test_thread_rpc.py -q
final exit 0: 27 passed

.venv\Scripts\python.exe -m ruff check src/thoth/domain/user_activity.py src/thoth/application/services/user_activity_projection.py src/thoth/application/services/research_progress_view.py src/thoth/application/services/research_completion_view.py src/thoth/application/commands/research_threads.py src/thoth/protocol/schema_export.py tests/unit/application/test_user_activity_projection.py tests/unit/application/test_research_activity_events.py tests/integration/test_user_activity_thread_read.py
exit 0

node .venv\Lib\site-packages\basedpyright\index.js src/thoth/domain/user_activity.py src/thoth/application/services/user_activity_projection.py src/thoth/application/services/research_progress_view.py src/thoth/application/services/research_completion_view.py src/thoth/application/commands/research_threads.py src/thoth/protocol/schema_export.py tests/unit/application/test_user_activity_projection.py tests/unit/application/test_research_activity_events.py tests/integration/test_user_activity_thread_read.py
exit 0: 0 errors, 0 warnings, 0 notes
```

The direct global Node invocation is recorded because the Python wrapper's bundled Node executable was blocked by Windows application-control policy before analysis began. The same installed BasedPyright `index.js` completed successfully with the allowed Node runtime.

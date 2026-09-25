# Stable error contract

Errors have three layers:

```text
protocol code     JSON-RPC/request/runtime failure
domain reason     stable machine-readable product state
public message    safe localized explanation
```

Initial required domain reasons:

```text
POLICY_DENIED
AUTHORITY_REQUIRED
STALE_REVISION
STALE_CHECKPOINT
CROSS_PROJECT_REFERENCE
EVIDENCE_INELIGIBLE
PROFILE_DECISION_REQUIRED
SANDBOX_REQUIRED
LOOP_BUDGET_EXHAUSTED
SEARCH_SATURATED
AMBIGUOUS_EXTERNAL_RESULT
MIGRATION_REQUIRED
CANONICAL_PROJECTION_DIVERGED
```

Clients and tests branch only on codes. Internal exceptions and provider text are redacted from public responses. Adding a reason updates its enum/schema, safe mapping and negative-path contract test together.


# THOTH backend NFR evidence matrix

Every material backend change reports these eight areas. `UNKNOWN` is required when a gate did not run.

| Area | Required evidence |
|---|---|
| Correctness | idempotency, concurrency, Unit-of-Work atomicity, state transition, retry/reconciliation |
| Security/privacy | authentication/scope where applicable, policy preflight, secret/egress/context isolation, retention |
| Recovery | migration replay, checkpoint/resume, backup/restore ownership, forward repair, ambiguous-result handling |
| Observability | operation ID, structured safe events, metrics/SLO ownership, runbook and runtime provenance |
| Compatibility/version | schema/policy/prompt/model/tool versions, additive migration, old-receipt replay |
| Performance/capacity/cost | bounded context, query count, bytes, time/token/call budgets, backpressure and retry amplification |
| Execution verification | unit, contract, failure injection, normal-entry acceptance, live adapter/E2E where claimed |
| Maintainability | layer/OCP, canonical owner, extension point, module/function responsibility ratchet, dead/duplicate policy |

Functional success cannot compensate for a critical security, atomicity, data-loss, hidden-holdout leakage or R3/R4 authority violation.

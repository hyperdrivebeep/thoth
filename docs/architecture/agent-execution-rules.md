# THOTH agent execution rules

These rules apply to every model call, evidence-acquisition wave, sandbox execution, memory transition and improvement experiment.

## Candidate boundary

Model output is untrusted candidate data. A model may propose SearchIntent, EvidenceLink, Claim, Hypothesis, Action, Criterion candidate, Memory candidate or Improvement candidate. Only typed schema, deterministic compiler, policy/authority gate and Unit of Work may advance canonical state.

## Coordinator-only tools

Models do not receive unrestricted filesystem, connector, sandbox, database, Git or external-action authority. They return typed intent. The Coordinator selects a registered capability, checks the bound policy snapshot, executes it and independently validates the result.

## Bound execution envelope

Every tool call records and validates:

```text
project and thread/object scope
actor/session when available
policy revision and digest
cutoff and temporal eligibility
current HeadSet/BaselineSet
input artifact and context-manifest digests
tool/adapter/runtime versions
security, egress and secret policy
time/token/call/byte/cost/retry/depth budget
```

## Finite loops

Every loop has a hard maximum and one terminal result: `SUFFICIENT`, `SEARCH_SATURATED`, `BUDGET_EXHAUSTED`, `POLICY_BLOCKED`, `AUTHORITY_REQUIRED`, `HOLD`, `ABSTAINED`, `FAILED`, or `COMPLETED`. It never silently extends depth or retries semantic failures in the same revision.

## Scientific status separation

```text
source discovered != observation valid
observation valid != claim supported
claim supported != outcome causally attributed
process succeeded != criterion met
criterion met != official disposition approved
valid hash != semantic truth
```

## Context and contamination

Active context is project-, authority-, cutoff-, security- and task-scoped. Future, oracle, hidden holdout, cross-project, expired, quarantined, secret and prompt-injection content is excluded or kept in its allowed non-reasoning lane. Retrieval outputs retain exact source/span/version/freshness metadata.

## Trace without private reasoning

Persist structured inputs, source references, candidate outputs, alternatives, reason codes, uncertainty, compiler/policy results, versions, operations and receipts. Do not persist private chain-of-thought, raw rejected provider output, secrets, or unauthorized source text.

## Memory

Memory write, recall and action eligibility are separate gates. `COMMITTED` does not mean universally recallable. Cross-project automatic recall is prohibited. Derived summaries, vector indexes and graph projections are rebuildable and non-canonical.

## Improvement

Improvement preserves immutable baseline and candidate. Candidate generation, evaluator, hidden holdout and promotion authority are separated. Promotion requires frozen comparison evidence and staged exposure; regression, leakage, budget or timeout triggers rollback. Official KPI, safety threshold, waiver, final disposition and production model-weight changes remain outside autonomous promotion.

## Development Hook ownership and final verification

The development workflow binds an implementation preflight to the actual host PreToolUse session and
creation turn through an immutable observed-request receipt. This is a workflow guard, not a security
boundary against a malicious process under the same OS user. No transcript or assistant AUDIT label
establishes ownership. Other sessions can perform read-only work and stop normally; they cannot edit,
complete or cancel the active owner's protected work. Later turns of the owning session retain its
obligation. An explicit owner release and new host-bound preflight provide the handoff path; legacy
receipt bytes/hashes are preserved and cannot silently become new owned authority.

At final source freeze, record Wiki synchronization and invoke complete_architecture_gate.py once.
It selects TOOLING or FULL using verification-profiles.md and the actual file-manifest diff against
a validated FULL baseline. Policy changes or unknown impact require FULL. TOOLING retains all
architecture/Hook/owner/portable/CLI regressions but explicitly has full_suite_verified=false.
It seals only real success with unchanged source/index/rules and matching Wiki/bundle/selection
evidence. FULL retains Makefile.ps1 verify once, including architecture checks. Focused development
RED/GREEN and affected checks remain required. Stop never runs tests or creates a new scheduler;
the owning session's bounded continuation distinguishes a node checkpoint from whole-plan completion.

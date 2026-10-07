# Public RPC method catalog

This source-preview index is derived from the public method catalog, registered runtime method names, and public source schemas. It lists available RPC names; it is not a live-service, account-authorization, model-execution, or full-suite verification receipt. The machine-readable metadata and notification names are in [`schemas/protocol/public-method-catalog.json`](../../schemas/protocol/public-method-catalog.json).

Requests use the public JSON-RPC `/rpc` surface. The method classification below distinguishes reads from commands; each call still follows the runtime's authentication and resource-scope checks. The six compatibility aliases remain listed under their own method names. The catalog contains 362 public methods (356 canonical and 6 aliases) and 221 notifications.

## Local model credential boundary

| Method | Kind | Current contract |
| --- | --- | --- |
| `model/credential/list` | QUERY | Reads workspace-local account and credential status without returning secrets. |
| `model/credential/register` | COMMAND | Registers an explicit API key or starts the selected provider's login route. Starting login does not establish connection or verified execution. |
| `model/credential/login/status` | QUERY | Reads the selected workspace-local auth method and optional `login_id`; the 5-second handler never refreshes remote auth or catalog. |
| `model/credential/login/cancel` | COMMAND | Requires `provider` and `login_id`; cancels only the matching attempt, preserving a prior credential. The handler has a 5-second bound. |
| `model/catalog/refresh` | COMMAND | Fetches the provider model lists once and returns the refreshed account rows and `catalog_status` (per provider: source, `ACTIVE`/`STALE_LAST_GOOD`/`UNAVAILABLE`, last fetch time, excluded models). A failed fetch keeps the last good list. Called after a login first reports CONNECTED, by the "load models" button, and once in the background at app start when a list is missing or older than 24 hours; `model/settings/read` and `update` use the stored list and never call it; status reads and polling never call it. LOCAL only; HOSTED_REVIEW denies before local I/O. |
| `model/tooling/install` | COMMAND | Installs one registered helper tool (`tool_id`, for example `codex` or `claude-code`) into its own folder under the THOTH tools directory and returns the refreshed account rows. Refuses a second install of the same tool while one runs. LOCAL only; HOSTED_REVIEW denies before local I/O. |
| `model/credential/login/complete` | COMMAND | Accepts a matching manual response for a method that supports it. The raw response is ephemeral and must not appear in operation replay, receipt or telemetry; HOSTED_REVIEW denies before local I/O. |

These credential RPCs use the `system:workspace` scope in LOCAL mode. HOSTED_REVIEW denies them before the local handler. The local setup and settings screens consume the login status and cancel methods through `XaiDeviceLogin`; they do not convert a status read into a new login start. A command's operation record, broker worker state, and workspace profile are distinct effects.

## Research request origin (trace row)

No method was added. `thread/start` and `thread/input` with `contract_version=2` accept one optional `origin` that names a row of the project's verification trace: `kind` (`TRACE_VERDICT`), `project_id`, `subject_kind` (`CRITERION` or `REQUIREMENT`), `subject_id` and the `verdict_revision` digest the client was looking at. Nothing else is accepted. The server checks the name against the stored trace with the same project read rule as `trace/read`, refuses a missing row, another project or a revision that is no longer the row's current verdict, and writes the facts itself (state, computed reasons, rule summary, chosen result IDs, source positions). That server-written origin is stored with the request attempt, given to the model as context with the instruction that the rule's verdict is not to be re-judged, pins the row's source positions into the evidence shortlist (only spans that are already retrievable), and appears in the result as `origin`. `thread/input` with an origin must target the thread whose own origin is the same row; `thread/steer`, `contract_version=1` and an origin of any other shape are refused. A resend of an already accepted request is not checked again. `thread/list` adds `origin` (`subject_kind`, `subject_id`, `verdict_revision`, or null) to each thread so a client can open the conversation of a row. The origin adds no model call and is not part of the thread's data scope or memory scope.

## Method index

## 1. Action Namespace

### Queries

```text
action/audit/read
action/authorization/read
action/impact/read
action/list
action/plan/graph
action/plan/read
action/policy/read
action/portfolio/list
action/portfolio/read
action/read
action/step/read
```

### Commands

```text
action/authorization/decide
action/authorization/prepare
action/compensation/create
action/create
action/draft/fromTest
action/generate
action/impact/recalculate
action/merge/propose
action/plan/compose
action/plan/revalidate
action/plan/revise
action/policy/classify
action/portfolio/compose
action/portfolio/evaluate
action/preflight/read
action/recommend
action/revise
action/select
action/step/add
action/step/revise
```

## 2. Closure Namespace

### Queries

```text
closure/audit/read
closure/list
closure/openItem/list
closure/package/read
closure/read
closure/readiness/read
closure/reopen/read
closure/retention/read
```

### Commands

```text
closure/decide
closure/finalize
closure/followup/create
closure/prepare
closure/purge/prepare
closure/readiness/assess
closure/reopen
closure/retention/plan
```

## 3. Criteria Namespace

### Queries

```text
criteria/audit/read
criteria/conflict/list
criteria/conflict/read
criteria/list
criteria/profile/list
criteria/profile/read
criteria/read
criteria/reference/list
criteria/reference/read
```

### Commands

```text
criteria/change/propose
criteria/compile
criteria/field/correct
criteria/profile/apply
criteria/recalculate
criteria/reference/generate
criteria/revalidate
```

## 4. Evidence Namespace

### Queries

```text
evidence/audit/read
evidence/conflict/list
evidence/conflict/read
evidence/list
evidence/packet/read
evidence/read
```

### Commands

```text
evidence/challenge
evidence/link/correct
evidence/link/propose
evidence/revalidate
evidence/source/add
evidence/source/metadata/correct
evidence/source/refresh
evidence/span/correct
```

## 5. Execution Namespace

### Queries

```text
execution/attempt/list
execution/attempt/read
execution/audit/read
execution/effect/read
execution/frontier/read
execution/list
execution/preflight/read
execution/read
execution/reconciliation/read
```

### Commands

```text
execution/cancel
execution/compensation/propose
execution/invalidate
execution/observation/link
execution/pause
execution/reconcile
execution/resume
execution/retry
execution/start
```

## 6. Export Namespace

### Queries

```text
export/artifact/list
export/audit/read
export/correction/read
export/list
export/manifest/read
export/plan/read
export/read
export/release/read
export/snapshot/read
export/verification/read
```

### Commands

```text
export/correction/create
export/generate
export/plan/create
export/prepare
export/release/prepare
export/snapshot/create
export/verify
```

## 7. Field Namespace

### Commands

```text
field/event/record
field/export/build
field/protocol/seal
field/score/record
field/session/end
field/session/start
```

## 8. Hypothesis Namespace

### Queries

```text
hypothesis/appraisal/read
hypothesis/assumption/list
hypothesis/audit/read
hypothesis/graph/read
hypothesis/link/list
hypothesis/list
hypothesis/portfolio/list
hypothesis/portfolio/read
hypothesis/prediction/list
hypothesis/prediction/read
hypothesis/quality/read
hypothesis/read
hypothesis/test/result/list
hypothesis/relation/list
hypothesis/review/list
hypothesis/same/list
```

### Commands

```text
hypothesis/appraise
hypothesis/assumption/add
hypothesis/causal/update
hypothesis/counterevidence/request
hypothesis/create
hypothesis/generate
hypothesis/intent/update
hypothesis/link/recheck
hypothesis/merge/propose
hypothesis/portfolio/compose
hypothesis/portfolio/revalidate
hypothesis/prediction/bind
hypothesis/refutation/record
hypothesis/relation/add
hypothesis/relation/remove
hypothesis/review/request
hypothesis/revise
hypothesis/same/record
hypothesis/split/propose
hypothesis/test/bind
hypothesis/test/result/record
```

## 9. Improvement Namespace

### Queries

```text
improvement/audit/read
improvement/canary/read
improvement/evaluation/read
improvement/evaluation/result/read
improvement/exposure/read
improvement/exposure/runtime/read
improvement/list
improvement/promotion/read
improvement/read
improvement/shadow/read
```

### Commands

```text
improvement/evaluation/assess
improvement/evaluation/plan
improvement/evaluation/run
improvement/exposure/complete
improvement/exposure/decide
improvement/exposure/prepare
improvement/exposure/rollback
improvement/exposure/start
improvement/promotion/decide
improvement/promotion/prepare
improvement/propose
improvement/retire/propose
improvement/revise
improvement/rollback/prepare
```

## 10. Investigation Namespace

### Queries

```text
investigation/audit/read
investigation/list
investigation/read
```

### Commands

```text
investigation/pause
investigation/resume
investigation/start
investigation/stop
investigation/update
```

## 11. Memory Namespace

### Queries

```text
memory/candidate/list
memory/candidate/read
memory/conflict/list
memory/context/read
memory/gate/read
memory/list
memory/policy/list
memory/policy/read
memory/projection/status
memory/read
memory/recall/audit
memory/revision/list
memory/settings/read
```

### Commands

```text
memory/candidate/classify
memory/candidate/create
memory/changeSet/propose
memory/context/build
memory/edit/propose
memory/projection/rebuild
memory/retention/evaluate
memory/retire/propose
memory/revalidate
memory/settings/update
memory/validate
```

## 12. Model Namespace

### Queries

```text
model/callSettings/read
model/credential/list
model/credential/login/status
model/settings/read
```

### Commands

```text
model/callSettings/update
model/catalog/refresh
model/credential/login/cancel
model/credential/login/complete
model/credential/register
model/settings/update
model/tooling/install
```

## 13. Object Namespace

### Queries

```text
object/attention/list
object/audit/read
object/candidate/list
object/candidate/read
object/impact/read
object/list
object/profile/list
object/profile/read
object/read
object/relation/list
```

### Commands

```text
object/attention/acknowledge
object/facet/update
object/followup/create
object/frame/revise
object/materialize
object/merge/propose
object/profile/apply
object/relation/add
object/relation/remove
object/revalidate
object/split/propose
object/work/replan
```

## 14. Operation Namespace

Shared query/control methods:

```text
operation/cancel
operation/checkpoint/read
operation/pause
operation/read
operation/result/read
operation/resume
```

## 15. Outcome Namespace

### Queries

```text
outcome/attribution/read
outcome/audit/read
outcome/changeSet/read
outcome/impact/read
outcome/list
outcome/profile/list
outcome/profile/read
outcome/read
outcome/series/list
outcome/series/read
```

### Commands

```text
outcome/assess
outcome/attribution/assess
outcome/changeSet/propose
outcome/followup/generate
outcome/impact/propose
outcome/observation/link
outcome/reassess
outcome/series/create
```

## 16. Project Namespace

### Queries

```text
project/cutoff/impact
project/list
project/policy/read
project/read
project/reference/list
project/review/list
project/role/list
project/source/list
project/source/scope/read
```

### Commands

```text
project/activate
project/archive
project/create
project/cutoff/update
project/delete
project/metadata/update
project/overlay/update
project/policy/update
project/reference/import
project/role/assign
project/role/revoke
project/source/connect
project/source/disconnect
project/source/scope/assign
project/source/scope/grant
project/source/scope/revoke
project/source/scope/update
project/source/time/confirm
project/source/time/correct
```

## 17. Projectpack Namespace

### Queries

```text
projectpack/list
```

### Commands

```text
projectpack/run
```

## 18. Receipt Namespace

### Queries

```text
receipt/audit/read
receipt/bundle/read
receipt/lineage/read
receipt/list
receipt/read
receipt/stream/read
receipt/verification/read
```

### Commands

```text
receipt/bundle/create
receipt/bundle/verify
receipt/correction/create
receipt/seal
receipt/verify
```

## 19. Revision Namespace

### Queries

```text
revision/audit/read
revision/baseline/list
revision/baseline/read
revision/changeSet/read
revision/compare
revision/conflict/list
revision/conflict/read
revision/content/read
revision/diff/read
revision/graph/read
revision/head/read
revision/history/read
revision/impact/read
revision/list
revision/read
revision/restore/preview
revision/timeline/item/read
revision/timeline/read
```

### Commands

```text
revision/baseline/decide
revision/baseline/prepare
revision/branch/create
revision/changeSet/commit
revision/changeSet/create
revision/changeSet/validate
revision/merge/propose
revision/merge/resolve
revision/propose
revision/recompute/request
revision/restore
revision/restore/apply
revision/restore/propose
```

## 20. Thread Namespace

### Queries

```text
thread/activity/list
thread/checkpoint/list
thread/checkpoint/read
thread/list
thread/read
thread/result/compare/read
thread/result/read
```

### Commands

```text
thread/fork
thread/input
thread/metadata/update
thread/pause
thread/resume
thread/start
thread/steer
thread/stop
```

## 21. Trace Namespace

### Queries

```text
trace/closure/list
trace/export
trace/history
trace/importPreview
trace/lesson/list
trace/read
```

### Commands

```text
trace/closure/record
trace/confirm
trace/importApply
```

## 22. Workspace Namespace

### Queries

```text
workspace/ready
workspace/setup/read
```

### Commands

```text
workspace/setup/update
```

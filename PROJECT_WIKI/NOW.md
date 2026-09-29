# Public source preview status

This is a source-only experimental preview. The private development timeline is not distributed.
Current verification and limits are recorded in [docs/VERIFICATION.md](../docs/VERIFICATION.md).

```text
ATOMICITY DEBT: 10 OPEN
```

## 2026-09-29 public authentication and restart source update

This experimental source update adds a common authentication-method contract, THOTH-owned
provider profiles, login status/cancellation and manual Claude response handling.
API keys and OAuth remain separate choices. Login does not change a saved model or effort.
Transient authorization URLs and device codes are excluded from durable operation results.

Codex uses its isolated profile and explicit model discovery. xAI preserves an existing
connection when a new login is cancelled and reports inconsistent credential files as HOLD.
The Claude OAuth/Messages adapter is implemented, but login is disabled without explicit
client configuration; permission to offer this third-party subscription route and real
provider execution remain unresolved. This is not a claim of supported live Claude login.

SQLite research records and object files remain the canonical local store. Controlled
process reopen/cold-copy checks preserve results and references without rerunning a model.
Damaged setup files and browser records are distinguished from first run and preserved;
existing research remains readable while execution requiring the missing setup is held.

Scoped backend and Web checks passed on their recorded stages, and the final Web bundle
builds. Independent r2 Chrome/RPC checks with synthetic providers accepted a partial scope:
explicit authentication routes, isolated workspaces, damaged-setup read access and saved
results reopened in a separate process. A QA diagnostic database-lock failure and unrun
scenarios remain recorded. This is not complete acceptance or real-provider output evidence.

A current-source FULL, physical Windows reboot, real account/provider acceptance, Codex
Desktop coexistence and field-effect evidence remain unverified. The source update is
published for inspection and local testing; the hosted service remains unchanged.

The latest recorded completed backend FULL had 1,694 passes, 41 failures and one skip;
that run did not reach Web or doctor. Later scoped repairs do not turn it into a pass.
The older 25-case selected ledger and source-archive digest are historical evidence.

See [model authentication](../docs/architecture/model-auth.md),
[installation and recovery](../docs/INSTALL.md) and
[verification limits](../docs/VERIFICATION.md).

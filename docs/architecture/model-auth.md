# Model authentication boundary

## Current default — 2026-09-13

The user approved the same-OAuth local-bounds route after the existing endpoint rejected
`max_output_tokens`. `CodexHttpExecutor` is now the default registered adapter. It reads the
existing Codex auth store only inside the adapter, does not copy it, refresh it, acquire another
account, or fall back to a paid API. Tokens never enter model inputs or persisted receipts.
Local received-byte/time limits and cancellation attempts do not guarantee upstream generated
or billed token ceilings or remote termination. See [dispatch controls](research-dispatch-controls.md).

The section below describes the earlier CLI broker, which remains a legacy adapter. Its
no-auth-file-read statement no longer describes the explicitly approved direct default route.

## Historical CLI broker contract

## Decision

THOTH delegates ChatGPT OAuth to the official Codex CLI instead of copying Hermes credentials, importing Codex auth files or implementing another product's OAuth client ID.

```text
THOTH ModelPort
├─ ScriptedModel
├─ OpenAI Responses API adapter
└─ Codex OAuth broker
   └─ official Codex CLI and its own auth store
```

## Invariants

- THOTH does not open, parse, copy, log or persist Codex/Hermes access and refresh tokens.
- `auth-status` exposes only connected/not-connected state.
- `auth-connect` delegates device authorization to `codex login --device-auth`; the user performs browser approval.
- model execution is `--ephemeral`, `--ignore-user-config`, `--ignore-rules`, `--sandbox read-only` in a new empty temporary directory.
- THOTH supplies one JSON Schema and consumes only the final structured message.
- input evidence is explicitly labelled untrusted and the model is told not to use tools or inspect files.
- failure diagnostics expose an exit code and typed state, not raw credential-bearing output.
- provider fallback is deny-by-default.

## Current status

- OAuth status: connected through official Codex CLI at the 2026-08-30 verification point.
- Synthetic structured-output canary: PASS.
- Main ProjectPack cycle: Codex OAuth provider is selectable from the CLI.
- Promotion evidence: one synthetic two-document cycle reached four hypotheses, three compiled actions, a safe frontier, three revisions, two memory records and one receipt.
- ScriptedModel remains the deterministic regression and sealed-replay provider.

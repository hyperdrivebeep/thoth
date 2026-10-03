# Model authentication boundary

## Common workspace authentication contract — 2026-09-28 candidate

The LOCAL credential facade dispatches `(account_provider, auth_method)` through registered
workspace handlers. `openai/codex_isolated_browser` selects `codex-oauth`,
`xai/xai_device_code` selects `xai-oauth`, and explicit `anthropic/claude_pkce` selects
`claude-oauth`. API-key routes remain separate. Omitted methods retain the existing OpenAI/xAI
defaults and Anthropic Claude Code guidance. The credential list keeps its old combined account
fields and adds an ordered `auth_methods` array with one API-key and one OAuth row; a key does not
mask the OAuth row or select its route.

`model/credential/login/status` reads only the selected local attempt/profile. It does not call
the token endpoint, `account/read`, or `model/list`. Start and cancel commands return short-lived
display data only to the first caller. The operation, event, checkpoint, receipt and idempotency
replay store an auth-specific allowlisted projection. Manual `login/complete` accepts a matching
workspace attempt under `system:workspace`, with a five-second reception bound; its raw callback
or code is never returned or persisted by that RPC. HOSTED_REVIEW rejects all five credential
methods before local I/O. A rejected or unsupported method does not silently switch provider.

The Codex broker's local status and cancel use its THOTH-only profile and opaque pending ID.
The Codex model list is kept per signed-in account as a typed snapshot in
`model-profiles/catalog/<provider>-<account digest>.json` (status `ACTIVE`, `STALE_LAST_GOOD`
or `UNAVAILABLE`; the account appears only as a one-way digest, never a token or account ID).
A fresh process shows the saved list at once, without calling App Server. `model/settings/read`
and `model/settings/update` use that stored list only; they never ask the provider. The list is
fetched by App Server `account/read` and `model/list` (a) when a login finishes, (b) when the
user presses "load models" (`model/catalog/refresh`), and (c) once in the background at app
start when the list is missing or older than 24 hours (one fetch per provider at a time, 20-second
deadline, stopped when the app closes). There is no timer. A failed or invalid candidate never
replaces the saved list: the old list stays as `STALE_LAST_GOOD` with the failure reason, and only
a lost login makes execution unavailable. Models left out of a list (`UNSUPPORTED_SLUG`,
`UNKNOWN_EFFORT_ONLY`, `NAMESPACED_ID`) are recorded with their reason. A request the provider
refuses for the model or account (not a network, timeout or quota failure) marks that model
`execution=REJECTED`; a completed research marks it `VERIFIED`. A saved selection is never
changed automatically. Merely reading credential status does not discover models. A saved list
is not proof that a request will succeed. This is a scoped local behavior statement, not evidence
of live Desktop coexistence.
The Codex HTTP adapter rechecks the same isolated account and access token under the profile
lock through the first physical send; a rotation after request preparation fails before network
I/O rather than silently using another account.

Codex model discovery and credential rotation are separate operations. A valid pinned THOTH
snapshot is reused for normal model preparation; an explicit list refresh can
call App Server `account/read(refreshToken=false)` and `model/list` without writing auth. Only an
access token within the five-minute expiry margin requests `refreshToken=true`, under the
profile owner lock. A refresh must produce a new, fresh same-account auth snapshot. Timeout,
schema uncertainty or a missing confirmed write leaves a THOTH-only digest marker and a typed
HOLD; reopening the broker does not silently send a second forced refresh. If the file later
changes to a valid fresh snapshot, non-refresh account/catalog validation clears the marker.
The marker contains only a SHA-256 digest of the prior auth file, never a token or account ID.

Codex and xAI first-send gates acquire their existing profile file locks asynchronously. Lock
preparation and each nonblocking probe run off the event loop; cancellation waits for an
in-flight probe and releases any late-acquired handle. The gate still validates the pinned
identity or credential generation and holds the lock through the first HTTP response headers.
The Codex prepared dispatch also binds the auth-file digest read with its account/model; a
same-account file rotation between prepare and dispatch is held before any POST. The gate
rechecks that same digest at physical send.
Synthetic concurrent dispatch and heartbeat checks establish local liveness, not remote
termination or a provider-side usage ceiling.

xAI login attempt identity is separate from the stored credential generation. Starting or
cancelling a new attempt preserves the previous valid credential. A successful attempt rotates
the generation, and the xAI Responses transport rechecks the credential under the profile lock
through its first physical HTTP send. A changed generation is rejected before send. Refresh
transport/schema ambiguity remains `XAI_REFRESH_OUTCOME_UNKNOWN`; explicit refresh HTTP 429 is
`XAI_REFRESH_RATE_LIMITED`, while 400/401/403 require reauthentication. No refresh retry or
account fallback is implied. These claims have synthetic transport evidence only.
If `auth.json` and `generation.json` disagree after an interrupted replacement or cold copy,
status is an explicit `XAI_AUTH_GENERATION_MISMATCH` HOLD and execution sends no model request;
saved research remains readable. The two files are still separate atomic replacements, not a
single crash-atomic transaction.

Claude OAuth and Messages are independently registered as a technical candidate. Without a
THOTH-owned registered client identity, `start` is unavailable; a route or catalog entry alone
does not establish third-party entitlement or successful inference.

## Claude Code account route — 2026-09-29 candidate

The `claude-code` route runs the official Claude Code executable in print mode with tools disabled.
Sign-in is that executable's own `auth login --claudeai`, started by THOTH in an isolated
`CLAUDE_CONFIG_DIR` (`model-profiles/claude-code`) and supervised as a process tree (Windows Job
Object). THOTH extracts only a claude.ai or claude.com link from its output for the screen, may
forward a pasted code to its stdin, and confirms the result through `auth status --json`. It never
reads or copies credential files, and it never installs or updates a global `claude`.

The route is admitted to research with `OBSERVATION_ONLY` control, like the Codex and xAI routes:
token usage is recorded from the executable's JSON result, but one process can still make more
than one provider request, so this is not a cost ceiling. Local eligibility requires a supported
version and a subscription login; `execution_verified` stays false until a real run succeeds.
Unverified with a real account: the login's terminal-less behavior, the exact `auth status --json`
shape, and whether the installed executable still accepts `--max-turns`.

## Isolated workspace connection — 2026-09-26 candidate

Codex login, status, refresh and model discovery are bound to the same THOTH workspace, executable
identity and dedicated profile. Existing authentication from other programs is not
imported. The login owner is the official Codex App Server in managed ChatGPT mode with a file
credential store. It is used for authentication and catalog requests, with no thread/turn execution.

Model inference remains on `CodexHttpExecutor`, which sends an explicit model, reasoning setting,
output schema and `tools=[]`. CLI inference is not treated as a substitute: a read-only sandbox
and a prompt asking the model not to use tools do not establish a native-tools prohibition.

The official account response does not expose the access token. A version-pinned adapter reads
only THOTH's dedicated auth file after the official owner refreshes it, checking freshness and
account/routing consistency before dispatch. THOTH does not refresh tokens itself. This bridge
depends on an internal file format and private backend endpoint, so its status is experimental;
it is not an official public API integration. Unsupported identities and incomplete refreshes
must fail before model I/O rather than fall back to another profile or provider.

`execution_eligible` describes whether the configured route can be attempted; `execution_verified`
describes observed provider success. Starting login, reading an account and listing models do not
by themselves verify model execution. Saved model/effort preferences remain user-owned.

Claude Code has a separate, unmodified-binary profile/login candidate. Its research route is held
until bounded physical request/retry behavior is established. Anthropic and xAI API-key routes are
independent. No other research application is a prerequisite.

## THOTH-owned xAI device route — 2026-09-27 candidate

`model/credential/register` with provider `xai` and no API key starts xAI device authorization,
returning only a trusted HTTPS verification URI, user code, expiry, opaque login ID and state.
`model/credential/login/status` and `/cancel` read or cancel this bounded broker job. The broker
handles pending, slow-down, denial, expiry and cancellation, and fences late polls by login
generation. Device/access/refresh tokens stay out of RPC results, catalog and receipts.

Credentials live only under the selected THOTH workspace at
`model-registry/xai-oauth-profile`. Atomic replacement, a cross-process owner lock and private
OS file permissions protect refresh rotation. Status and catalog reads are local and do not
refresh. An execution may refresh; an ambiguous refresh outcome is persisted as HOLD, preventing
a second refresh or model POST until a new login. The API-key provider remains `xai`; OAuth
execution is the separately selected `xai-oauth` provider. No default model or effort is changed
by login. The catalog advertises only the curated `grok-4.6` and its xAI source-supported
low/medium/high/xhigh effort map after THOTH-owned auth is present.

The public client settings and model metadata were observed in MIT-licensed
`@code-yeongyu/senpi-ai` 2026.9.26 (`auth/oauth/xai.js` SHA-256
`d34783560dc2ac75d6e717b248136eb75a902fbd53b3e56fb9f2ec50c6c793d7`, `xai.json`
SHA-256 `9b23faa5218e966a17108c05d2498a617ade1eae9b0d0684284c77d67949a3f1`).
THOTH does not read other programs' authentication or model caches on its default runtime route. The installed source uses
`auth.x.ai` device/token endpoints and the xAI Responses transport at `api.x.ai/v1`; xAI's
[Grok Build documentation](https://docs.x.ai/build/enterprise) describes device login but
distinguishes its inference endpoint from the [direct API-key Responses endpoint](https://docs.x.ai/developers/quickstart).
Official support for this third-party client and subscription-token/Responses combination is
UNKNOWN. `execution_eligible` means local credentials, catalog, selection and policy permit an
attempt; `execution_verified` remains false until a real successful request. Unsupported client,
401/403 and entitlement responses are typed holds. This candidate has synthetic HTTP evidence
only; no THOTH account login, refresh or model call has been verified.

The local workspace can remain readable when model execution is unavailable. Existing resource
authorization still applies. Data-root and browser-draft persistence are described in
[installation and restart behavior](../INSTALL.md#data-location-and-restarting).

Independent synthetic Chrome/RPC checks accepted a partial scope on the r2 snapshot.
Complete acceptance, actual provider calls, Desktop coexistence and a PC power-cycle
remain separate, unfinished evidence scopes; see
[verification](../VERIFICATION.md). The historical observations below do not validate this candidate.

## Historical direct default — 2026-09-13

The user approved the same-OAuth local-bounds route after the existing endpoint rejected
`max_output_tokens`. `CodexHttpExecutor` became the default registered adapter. It read the
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

## Historical verification point — 2026-08-30

- OAuth status: connected through official Codex CLI at the 2026-08-30 verification point.
- Synthetic structured-output canary: PASS.
- Main ProjectPack cycle: Codex OAuth provider is selectable from the CLI.
- Promotion evidence: one synthetic two-document cycle reached four hypotheses, three compiled actions, a safe frontier, three revisions, two memory records and one receipt.
- ScriptedModel remains the deterministic regression and sealed-replay provider.

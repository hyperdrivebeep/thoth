# Verification and known limits

## Current merge gate (2026-10-03)

The merge/publication gate is the product check defined in
[verification policy](architecture/verification-profiles.md). The candidate retains an unresolved Python failure;
no complete current-source product-check pass is claimed. Historical FULL results below are
retained as historical evidence, not current merge requirements or a new pass.

## Recorded product-check follow-up (2026-10-03)

The first completed candidate product check had 2,135 Python passes, 40 failures,
one skip and one expected failure; 139 inactive development diagnostics were excluded.
That failure result is retained. A correction keeps optional stream observations absent
instead of converting missing values to decimals. Scope lookup tests now require fresh
approvals for each traversal, and permission-recall fixtures explicitly ask about previous
research to satisfy the independent narrow-recall gate. Focused results do not replace a
complete product-check result. The final-source rerun of those 40 failures passed 39 cases and retained one baseline failure.
A new complete-suite rerun was stopped when verification was explicitly narrowed to failed cases.
The complete result and focused result are reported separately.

Web verification passed 409 cases with one skip; lint, types, build and runtime layer checks passed.
The final first-setup screen shows all three providers disconnected and its Next button disabled;
local doctor passed eight checks. The Web source hashes are unchanged, so its verified build was
reused for this screen check. Real-account login and provider response acceptance remain unverified.

A synthetic finalization-fault/reopen case also fails on the unmodified source baseline:
the same action can execute again when its compiled input files shrink from two to one,
while the previous execution's result remains unadmitted. The current guard compares exact
input bundles. This input-change recovery risk is unresolved and requires reconciliation
before relying on that path for effects. No release-readiness claim is made for it.

ATOMICITY DEBT: 10 OPEN

A bounded slice D4/D5 does not certify full namespace atomicity.
Claude Code and xAI real-account login and response acceptance remain unverified.


## 2026-09-29 public source update and acceptance boundary

Authentication and restart changes are published as experimental source before live acceptance.
Publication does not mark product verification complete. Scoped checks cover common
credential RPCs, provider/profile isolation, cancellation, ephemeral login responses,
credential-change refusal before dispatch, setup-file integrity and saved research reopen.
The Web changes cover separate API-key/OAuth states, explicit method selection, preserved
model/effort and readable research when setup or browser records are damaged.

Recorded stages include core-auth 65 selected passes followed by four affected Codex cases,
Claude 29 selected passes followed by one affected assertion, storage 24 selected passes
and 29 impact cases, and Web 107 selected passes followed by the final 83-case affected
first-run/restart scope. Subsequent narrow assertions and source hashes are kept separately.
These sets overlap and must not be added into an overall pass count. The final Web build
passed; its existing large-chunk warning was not suppressed.

An additional auth review found unnecessary forced Codex refreshes and blocking first-send
lock waits. The corrective scope passed 44 Codex and 31 xAI cases, including zero forced
refreshes for valid-token requests, one necessary concurrent refresh, uncertain-refresh HOLD,
prepared credential-change refusal, event-loop responsiveness and cancellation cleanup.
Independent Chrome r1 preserved four regression passes and partial Codex/xAI observations,
then stopped for that correction. Those observations remain tied to r1.

The subsequent independent r2 check used real Chrome, the normal local RPC path, registered
provider serializers and synthetic HTTP transports. It accepted a partial scope covering
explicit API-key/OAuth selection, workspace separation, damaged-setup warnings with existing
research still readable, and five saved results reopened in a separate process without model
re-execution. This proves neither live provider access nor the quality of a research answer.

An initial QA diagnostic held a read-only SQLite connection across an RPC and produced a
database-lock HTTP 500. Closing that diagnostic connection allowed the check to continue;
the original failure is retained. Database file bytes changed during reopen, with the exact
cause unisolated; only the recorded result identities/revisions/content were compared.
Direct browser localStorage inspection and further negative browser scenarios were not run.
The r1 four-case regression was not repeated in r2 and is not relabelled as an r2 test pass.

The published runtime/test/dependency files match that r2 snapshot; publication changes only
status documentation and the source manifest. The 2026-09-29 local server responded normally,
but first-party authentication was not completed and no real provider analysis, IRIS example
or FocuSee recording was completed. Account-specific authentication details are not published.

Complete independent acceptance and current-source FULL remain unfinished. The latest
completed backend FULL before these changes recorded 1,736 cases: 1,694 PASS, 41 FAIL and
one SKIP. Web/doctor were not reached in that run. The historical selected 25-case ledger
below is a different earlier scope, not the failure count of this working candidate.

Claude's new adapter supports the configured HTTP protocol path, but its default client
configuration is absent and login stays disabled. No third-party Claude.ai permission,
live OAuth entitlement, actual provider output quality, Codex Desktop coexistence or
physical Windows reboot is certified. Synthetic transports and process interruption/cold
copies are not evidence of those external outcomes. No verifier policy, inactive Hook
selection or assertion threshold was relaxed.

Known limit (decision record 2026-10-01): in the hosted-review mode only, retransmitting the same idempotency key after a restart seals the queued research operation as stale instead of returning the original receipt. Local mode is unaffected; the test is marked as an expected failure and the fix is deferred.

Known limit (decision record 2026-10-03): if a sandboxed research test runs but saving its result fails, and the test's input files shrink before the workspace is reopened, the duplicate-run guard treats it as a new test and runs it a second time. The earlier execution record is kept. The case exists since the first public source; its test is marked as a strict expected failure and the fix (resolve the pending result before any rerun) is deferred.

## Recorded feature evidence

- Minimal queue/strict-CAS/exact-result comparison closeout: 4 selected integration tests passed,
  with unchanged source before/after and verified command, log and JUnit identities.
- The unchanged comparison UI retains prior local Chrome evidence: one exact RPC, bounded default
  text, expandable detail, preserved selection, keyboard use and no overflow at 390px. This is not
  a fresh browser run on this public candidate.
- Queue/reentry/crash scope: 19 selected tests passed on its recorded source.
- Producer-selection/recovery scope: 28 selected tests passed. Provenance completeness did not
  relabel partial research or HELD learning as fully verified work.
- Policy publication/migration scope: 10 selected tests passed, including synthetic existing-row
  preservation and safe refusal of a destructive duplicate-content downgrade.
- First-run authentication/model boundaries: 14 controlled local acceptance cases passed.

These scopes overlap. Their counts must not be added into an overall pass count.

## Repository status

An earlier full run reached 1,501 backend passes and 86 failures. Its architecture, lint and type
stages passed; Web and doctor were not reached. Under the existing active-environment policy,
108 explicitly inactive development Hook cases were deselected.

An earlier scoped follow-up resolved 61 of those original failing nodes and left 25 open.
In the later v4 FULL, exact case IDs for that 25-case ledger show 22 FAIL and 3 PASS. This is not
a baseline for every other test; the full v4 run separately recorded 108 failures. No
current-source FULL pass is claimed; later type and scoped checks are recorded below. The
historical remaining nodes are in
[KNOWN_TEST_FAILURES.json](KNOWN_TEST_FAILURES.json).

Remaining cases concern source-time prerequisites, export/restore boundaries, Hero/sandbox paths,
evaluation/currentness and other display/contract mismatches. Some causes remain uncertain.
Assertions, selectors and time budgets were not relaxed to portray a passing full suite.

Canonical-owner atomicity debt remains open. Focused transaction evidence does not establish
complete atomicity for all owners or execution paths.

## Candidate checks

### 2026-09-26 isolated connection and restart candidate

This working candidate changes Codex credential ownership, local workspace selection and browser
draft restoration. The earlier v2 acceptance passed architecture, lint, Web checks (225 tests
passed, one existing skip) and doctor, but failed TYPE and backend collection as recorded in the
package-repair section below. The combined v3 candidate adds package repairs and xAI login;
its independent acceptance is pending. Earlier results remain tied to their recorded sources.
Controlled real-account OAuth/provider acceptance, Codex Desktop coexistence and a physical PC
power-cycle remain unverified for this candidate. The later xAI test isolation incident is
recorded separately below. The hosted service remains separate and unchanged.

The optional official `@openai/codex@0.157.1` package was installed in a fresh Windows x64 prefix
using `--install-strategy=nested`, with isolated npm configuration/cache and synthetic home paths.
Installation and the native executable's `--version` both exited 0; the version was
`codex-cli 0.157.1`. The wrapper/platform package and both npm lockfiles matched the recorded
official integrity values. The native executable was 322,515,248 bytes with SHA-256
`8cb0e69e99ff2a158c54815db82d0f2e524d8f301bc30184722cfd1ae5973574`.
This package-preparation check did not run THOTH, App Server, authentication or inference, and
did not update an existing global CLI or Desktop installation.

### 2026-09-26 Codex first-run repair candidate

The public source candidate was installed with frozen uv and pnpm locks in an isolated WSL copy.
The copy's changed runtime and Web files were SHA-256 checked against the Windows worktree.
Python 3.12.3, Node 24.19.0 and pnpm 11.21.0 were used. No provider model call was made.

| Check | Result |
| --- | --- |
| Codex credential/catalog unit tests and related first-run/model-settings integration files | PASS |
| Architecture check, Ruff, changed-file BasedPyright, doctor | PASS |
| Web ESLint, TypeScript, 189 tests, production build | PASS; one existing Web test skipped |
| Chrome first-run with CLI login but no visible model catalog | PASS: login action disabled and missing-route guidance shown |
| Chrome first-run with an explicit non-secret model-cache path | PASS: eight Codex model options found; Next advanced to step 2 without a model call |
| POSIX HTTP device-login request and immediate health check | PASS: manual-terminal guidance in 256 ms; `/healthz` 200 |
| Repository-wide pytest | FAIL at collection: four existing test files import `apps.api`, which is absent from this source-only package |
| Repository-wide BasedPyright with all optional extras | FAIL: 17 errors confined to the same four `apps.api`-dependent test files |

The full-suite failure is not represented as a pass. A separate diagnostic pytest run that ignored
those four collection-error files encountered further failures and was interrupted at 17%; it is
not a completed regression result. The candidate's focused checks do not establish remote Codex
entitlement, model output quality, or a complete public-tree regression pass.

### 2026-09-27 public package repair candidate

The later v2 frozen verification still reports TYPE failure (216 errors) and four backend collection
errors; those outcomes are preserved in the private v2 receipt. This subsequent unfrozen candidate
restores the missing API compatibility import and QA scenario modules plus the THOTH-authored
synthetic ProjectPacks required by their existing tests. The same four files now collect all seven
original tests. Focused execution exposed an additional source-time prerequisite: an undated
source remained `UNKNOWN_TIME`, so managed sandbox execution correctly held before adapter I/O.
The QA runner now applies only exact, hash-bound synthetic fixture time claims through the public
`project/source/time/confirm` RPC with a fresh source version, project revision, cutoff and metadata
digest. An absent claim still holds; outcome-only after-cutoff fixtures remain excluded.

In the candidate-only venv, installing the already-declared locked optional extras reduced TYPE
errors from 216 to 24 after `browser`, `connectors`, and `managed`, then to zero after
`structured-pdf`. No model weights, browser binaries, real provider calls, or global packages were
used. Focused checks and exact commands are in the private B repair receipt. A new source-frozen
FULL run and independent browser acceptance remain pending; this subsection is not a release claim.

### 2026-09-27 xAI device-code candidate

The candidate connects a THOTH-owned xAI device flow, credential persistence/refresh and an
explicit `xai-oauth` model route to normal research entry. Synthetic backend checks cover login
states, cancellation, refresh failure, separate workspaces, API-key coexistence and a stored
result read after reopening. Backend owner checks do not replace independent FULL or browser
acceptance. The shared first-run/account-panel device-code consumer is implemented. Its final
focused Web run passed 58 tests across first-run, model settings and RPC contracts; Web lint,
typecheck and build passed. The backend guard/repr follow-up passed 47 focused tests and its
targeted type/lint checks. These scopes overlap prior runs and are not a combined pass count.
The v3 independent assessment is recorded below; its blockers are being repaired in a v4 candidate.

An early account test omitted its fake broker and sent at least one real device-start request.
Its exact request count and any subsequent token-endpoint polling are unknown. No browser
approval was performed; no real model send was observed in the local call path. The test process
exited. This incident is not live acceptance. The affected tests now use synthetic transports;
their reviewed guard rejects missing, explicit real, mounted and proxy transports and also
blocks real HTTP send methods. Targeted regression checks passed without external requests.
Real THOTH login, refresh, entitlement and model success remain
unverified. Readiness metadata does not claim official third-party support.

### 2026-09-27 v3 independent assessment and narrow v4 repairs

Frozen v3 `model-connection-6694a4b00c66d702` kept all 1,404 source files and 19 Web build files
unchanged through independent verification. The standard FULL ran once: architecture, lint and
type passed; backend exited 1 after 7,272.44 seconds, so Web and doctor were not reached.
The private test runner omitted Git from PATH, preventing the optional progress plugin from
recording per-test telemetry. This run's failing case IDs and counts are unknown; the historical
25-case list is not substituted for them. The failed FULL is preserved and is not a release pass.

Chrome, using actual local THOTH RPC and fake provider services, found two independent xAI
blockers: the Web assumed an explicit LOCAL mode absent from the real readiness response, and
credential-start returned HTTP 500 when a float expiry reached canonical operation storage.
The v4 repairs consume the actual local readiness contract and serialize public expiry as integer
Unix seconds. The expiry regression reproduced the original HTTP failure before the fix and then
verified successful RPC completion, stored operation readback, status/cancel and same-key replay
with one synthetic device start. Final v4 independent verification remains pending. No external
provider approval link or real model was used in the v3 browser assessment or v4 expiry repair.

The narrow v4 owner checks passed 27 backend tests and 64 Web tests in their respective scopes;
these are not an aggregate FULL result. The Web readiness fixture was compared with the actual
`LocalWorkspaceReady` serializer without an added mode field. Both screens accept a valid readable
local workspace and continue to reject hosted, malformed and unreadable states. Targeted backend
type/lint and Web lint/type/build passed before the new independent source freeze.

The corrected private runner includes Git and checks identity capture before another FULL. A
separate two-case synthetic probe recorded one intentional failure, its case ID, completed
progress and JUnit output. That checks observation reliability and is not a product test pass.

### v4 complete result and subsequent Web settle repair

The v4 normal Chrome/RPC path fixed both v3 blockers using synthetic provider responses. It
preserved explicit `xai-oauth/grok-4.6/high` selection, a stored partial/TERMINAL result, reload
and workspace isolation. This was not real provider authentication or answer-quality acceptance.
The single v4 FULL completed 1,701 backend cases: 1,592 PASS, 108 FAIL, 1 SKIP and no collection
errors. Architecture, lint and type passed; the backend exit was 1, so Web and doctor were not
reached by that FULL. Complete safe telemetry preserved failed case IDs, but not private assertion
details. Individual causes remain under investigation; 108 is not the inactive-Hook exclusion count.

Separate Web checks passed lint/type but reported 214 PASS, 23 FAIL, 1 SKIP and one additional
Python-backed contract suite startup error. A separate build passed. Windows CodeIntegrity blocked
the unsigned candidate venv Python launcher under Smart App Control; this prevented doctor and
Python-dependent checks from launching. No security policy was weakened or alternate launcher
used. The later observed block is not proof of every earlier backend failure's cause.

The Web settle repair reproduced 21/22 restart failures. A child account panel subscribed to its
parent's readiness query, causing refetch, parent suspension, unmount and remount. The parent now
owns that query and passes its validated workspace ID to the panel. Permission rechecks, denied
access, draft preservation and stale-response fences remain in place. The repaired restart cases
and two new regressions passed; an explicit Node-only run passed 80 tests. A final test-only
assertion also passed its focused case. Web lint/type/build passed. These owner results do not
replace independent browser/FULL verification or close the 108 backend failures.

One pnpm argument-forwarding mistake selected the whole Web suite during this repair. Its result
was exit 1 (237 PASS, 2 Python-spawn FAIL, 1 SKIP plus a contract suite startup error); blocked
Python execution did not succeed. That attempt was preserved and not repeated or relabelled PASS.
At that point Python-dependent verification was blocked. The subsequent permitted recovery and
its scope are recorded below; the original failed attempts remain unchanged.

### 2026-09-28 signed runtime and scoped follow-up

The approved PSF Python 3.13.15 installation and its newly created venv both had valid signatures.
The original environment was preserved, locked dependencies were installed with an explicit
interpreter and managed/downloaded Python disabled, and a synthetic child process passed.
Windows application-control policy was unchanged. This resolved the observed launch block on
the recorded computer; it is not a universal Windows compatibility claim.

Independent v5 acceptance then passed standard Web lint/type/tests/build (247 PASS, one existing
skip) and doctor (8 checks). Actual Chrome and THOTH RPC with fake providers passed project
creation-to-settings, explicit xAI selection, stored partial results, reload, draft restoration,
permission recheck and workspace separation. Archive revocation used an actual RPC once followed
by browser readback when the browser confirmation control timed out. Real authentication, token
refresh, provider entitlement and a physical PC power-cycle remain NOT_RUN.

Subsequent source fixes have scoped evidence, not a new complete regression pass:

- Self-contained synthetic preflight fixtures: 39 active tests passed, five already inactive
  Hook cases deselected. No live private preflight was copied or Hook re-enabled.
- Official pinned Playwright assets restored synthetic browser execution: 14 browser cases passed.
- Explicit source-time claims in synthetic export fixtures: 18 cases passed, including exclusion
  of unconfirmed material and preservation of the frozen export scope.
- Public source-only package contracts: 31 cases passed. These validate public anchors and
  exclusions while preserving internal-tree checks. Renamed contracts retain old-to-new case
  mappings; hosted image validation remains `HOSTED_IMAGE_NOT_VERIFIED`.
- Missing login status/cancel declarations and fixture close ownership: 29 parity/inventory/A13
  cases passed, with zero failures, errors or skips. Atomicity coverage and related synthetic
  credential checks passed separately. Runtime broker ownership guards were unchanged.
- Catalog regeneration: eight builder/parity cases passed. Two writes to a test-only temporary
  output reproduced the reviewed catalog bytes, including credential metadata, without changing
  the actual catalog or inventory.

These groups overlap and must not be summed. Architecture/lint/type checks passed in their
recorded affected scopes; the ten canonical-owner atomicity debts remain OPEN. The historical
v4 FULL still records 108 backend failures; focused repairs do not relabel that run or establish
how many failures remain on the final candidate. A new frozen-source FULL is still required.
Claude's native execution contract and real-account/Desktop coexistence remain unverified.

### Historical published ZIP check after the earlier first-run repair

After the earlier 2026-09-26 first-run repair reached GitHub `main`, a fresh **Download ZIP** archive
extracted to `thoth-main`. This predates the package repair and xAI candidate above.
All 1,337 files named by `SOURCE_MANIFEST.json` matched their byte length and SHA-256. Frozen uv
sync, frozen pnpm install, Web build and doctor passed from the extracted source. This used an
existing WSL Python/uv installation and an existing Windows pnpm store; it does not prove
first-time tool installation on a new PC. The archive check exposed the README's clone-only
`cd thoth` step for ZIP users, corrected in the subsequent documentation update.

The private clean Windows check completed on 2026-09-25:

| Check | Result |
| --- | --- |
| Fresh frozen uv and pnpm installation | PASS |
| Doctor: Python, FTS5, base libraries, writable workspace parent | PASS |
| Three synthetic evidence UI test files | 36 PASS |
| Initial Web build | FAIL: missing direct Node type declarations |
| Exact `@types/node=22.20.4` + lockfile correction, frozen sync and dependent build | PASS |
| New workspace health, built UI HTML/asset and setup/ready/credentials/projects queries | PASS |
| Chrome first-run screen, three unconnected providers, disabled next button, console | PASS; zero console errors |
| Owned server/tab cleanup | PASS |

The initial failure remains part of the record. Python install, doctor and the unchanged 36 Web
tests were reused after the type-only dependency change. Code, test and fixture inputs were
unchanged across the final build/smoke window. The resulting workspace had zero credentials,
projects and operations; model readiness false was expected. No login or model call was started.

Tool versions were Python 3.13.15, Node 24.19.0, pnpm 12.6.0 and uv 0.12.5. Public status documents
were finalized after the execution window. These checks do not establish real credentials,
real-model quality, external sandbox operation, hosted multi-runtime safety, production readiness,
publication or actual collaborator usefulness.

## Developer configuration

`.codex/hooks.json` is explicitly empty. Together with the unchanged
`config/inactive-development-hook-tests.json`, it preserves the recorded environment's selection.
Personal Codex settings, local verification receipts and inactive backups are excluded.

Rights-unconfirmed optional assets may be absent from this public package, so asset-dependent
legacy checks can have additional unavailable-input failures. That package boundary is disclosed
in [PACKAGING.md](PACKAGING.md); tests were not deleted or skipped to conceal it.

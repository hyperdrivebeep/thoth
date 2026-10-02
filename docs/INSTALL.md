# Source installation and local operation

This preview targets a Windows source checkout with Python 3.12 or 3.13. Keep the root uv lock,
pnpm lock/workspace, migration tree and protocol schemas together.
Git clone creates a `thoth` folder. GitHub's **Code → Download ZIP** extracts to `thoth-main`;
open PowerShell in whichever repository folder you obtained before running these commands.

## Prepare Python on Windows

For the reviewed Windows x64 path, install the official PSF
[Python 3.13.15 Windows installer (64-bit)](https://www.python.org/downloads/release/python-31315/).
Use the normal per-user installation and note its directory. The example below uses the usual
`%LOCALAPPDATA%\Programs\Python\Python313` location; change `$thothPython` if you chose another.
Python's [Windows installation guide](https://docs.python.org/3.13/using/windows.html) describes
the installer options. The project supports Python 3.12 or 3.13; this specific recovery was
verified with PSF 3.13.15.

From a fresh checkout without `.venv`, create the environment with that interpreter:

```powershell
$thothPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
if (-not (Test-Path -LiteralPath $thothPython)) { throw 'Set thothPython to your installed PSF python.exe path.' }
$thothSignature = Get-AuthenticodeSignature -LiteralPath $thothPython
if ($thothSignature.Status -ne 'Valid' -or $thothSignature.SignerCertificate.Subject -notlike '*Python Software Foundation*') {
    throw 'Verify the official Python installation before continuing.'
}
if (Test-Path -LiteralPath '.venv') { throw 'An environment already exists; see Existing environments below.' }
& $thothPython -m venv --copies --without-pip .venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
if ((Get-AuthenticodeSignature -LiteralPath '.\.venv\Scripts\python.exe').Status -ne 'Valid') {
    throw 'The new Python launcher signature could not be verified.'
}
```

Then install the locked packages using the existing environment explicitly:

```powershell
uv sync --python .\.venv\Scripts\python.exe --no-managed-python --no-python-downloads --frozen --extra dev --dev
if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
pnpm.cmd install --frozen-lockfile
if ((Get-AuthenticodeSignature -LiteralPath '.\.venv\Scripts\python.exe').Status -ne 'Valid') {
    throw 'The Python launcher signature changed; inspect the environment.'
}
.\.venv\Scripts\python.exe -m thoth.cli doctor
.\.venv\Scripts\python.exe -m thoth.cli workspace
```

The [uv Python selection options](https://docs.astral.sh/uv/concepts/python-versions/) keep this
command on the selected installed interpreter and prevent an automatic Python download. Module
execution uses the reviewed Python launcher directly. Environment activation is unnecessary.

### Existing environments

Keep an existing `.venv` and research workspace intact. If Windows blocks that interpreter,
record the exact executable/signature and error; do not disable Smart App Control to run it.
The observed unsigned-launcher case was resolved with a normal PSF installation and a new signed
venv. This is evidence for that environment, not a guarantee for every Windows policy.
A fresh source checkout can use the setup above and reopen the same absolute research directory
with `serve --workspace ...`; see [data location](#data-location-and-restarting). Stop the old
backend before reopening its workspace from the new environment.

## Dependencies and Web interface

The development extra supplies pytest, Ruff and BasedPyright; the development group includes
reportlab for fixtures. Optional extras are separate:

- `structured-pdf`: Docling; its model assets and resource requirements are separate.
- `browser`: Playwright-based acquisition; browser installation is separate.
- `connectors`: PostgreSQL, S3 and MCP SDKs.
- `managed`: managed sandbox SDK.

The basic UI does not require enabling every optional adapter. An unavailable adapter remains
unavailable until it is deliberately configured.

Repository-wide static type verification imports the optional adapter modules. In a separate
verification checkout, install the locked `dev`, `browser`, `connectors`, `managed`, and
`structured-pdf` extras before running BasedPyright. This prepares Python packages only;
Playwright browser binaries, Docling model weights, credentials, and model requests are separate
steps and are not needed for static type checking. Keep the explicit `--python`,
`--no-managed-python` and `--no-python-downloads` options when syncing additional extras.

If you need the optional browser adapter or its integration tests, install the locked `browser`
extra and prepare its pinned assets in a dedicated cache. In the terminal running that backend
or those tests:

```powershell
uv sync --python .\.venv\Scripts\python.exe --no-managed-python --no-python-downloads --frozen --extra dev --dev --extra browser
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $env:LOCALAPPDATA 'THOTH-Verification\Playwright'
.\.venv\Scripts\python.exe -m playwright install --only-shell chromium
```

The cache variable applies to this terminal and its children; set the same value in a new
terminal before browser use. The existing lock selected Playwright 1.62.0/headless shell revision
1234 in the recorded check. The [official browser guide](https://playwright.dev/python/docs/browsers)
documents this separate asset installation. It does not modify your Chrome profile. Windows
Sandbox is not required to install or use THOTH.

Run the API and Vite in two terminals as shown in the README. For a production Web build:

```powershell
pnpm.cmd --dir apps/web run build
```

`apps/web/dist` is generated output. Desktop/backend-served UI modes require that build; the API
and Vite development interface can run separately.

After building, the local backend also serves the built UI on its own origin. The isolated smoke
used port 18765 to avoid an existing server and confirmed its HTML, JavaScript asset and first-run
screen. No existing workspace is needed for that check.

## First run and credentials

Use THOTH's setup/settings flow and keep the backend and connection commands on the same workspace.
API keys and model preferences belong to that workspace. Existing credentials from other programs
are not imported. A configured connection and a successful provider response are separate states.

| Connection | Behavior |
| --- | --- |
| OpenAI, Anthropic and xAI API keys | Register your own provider key, then select an available model. |
| ChatGPT / Codex | Experimental, version-pinned bridge using an official standalone Codex CLI and a THOTH-only profile. Login, token refresh and model discovery share that profile. |
| Claude (official Claude Code) | Sign in from the Claude card. THOTH runs the official Claude Code executable with a THOTH-only `CLAUDE_CONFIG_DIR` and never reads its credential files. Research runs use that executable's no-tools print mode and record token usage as an observation, not a cost limit. Controlled with a fake executable only; real-account sign-in and answers are unverified. The client-ID Claude OAuth route stays hidden until an app registration exists. |
| xAI subscription login | Experimental device-code route using a THOTH-owned workspace profile. OAuth models use the explicit `xai-oauth` route; existing `xai` API-key connections stay separate. Controlled Chrome/RPC flow passed with fake providers; real-account acceptance remains pending. |

The Codex CLI and Claude Code are helper programs. When one is missing, its card offers an automatic
install (Node.js required) into `%LOCALAPPDATA%\THOTH\tools\codex` or `...\claude-code`. It installs one
pinned version, checks it, and never touches a global install or another program's login. The
manual commands below do the same thing.

For Codex, use THOTH's connection action. A started login is not a completed login; refresh the
connection status after finishing in the browser. Missing CLI, unsupported version, missing login,
unavailable model catalog and unverified execution have separate diagnostics. Do not copy your
Desktop `auth.json`, configuration or model cache into THOTH. A bare `codex login` targets that
CLI's own profile and does not connect this workspace.

The Codex bridge delegates authentication and refresh to the official App Server, but keeps model
requests on THOTH's existing transport with native tools disabled. Its version-pinned auth-file
reader and private backend endpoint make this an experimental integration, not a supported public
OpenAI API contract. Real-account coexistence and provider execution require separate verification.

No account configuration is distributed here. Keep data directories and credentials out of Git.

For the xAI candidate, start login in THOTH, then use the displayed user code and HTTPS approval
link. Starting a flow does not mean approval completed. Pending, denied, expired and cancelled
flows have separate states; selecting an OAuth model is explicit and does not replace an existing
API-key selection. THOTH owns token persistence and refresh in its workspace and does not import
other programs' authentication or model-cache files. Connection/status reads do not call a model.

The device-code configuration follows the pinned open-source Senpi integration. Its use of the
xAI Responses endpoint is an implementation reference, not a promise of official third-party
support or subscription entitlement. Local readiness and a verified provider response remain
separate. Client rejection, revoked authorization and entitlement failures stop with a stated
reason; THOTH does not silently switch to a billable API-key route.

### Optional Codex prerequisite on Windows x64

The candidate is pinned to the official [Codex CLI 0.157.1 release](https://github.com/openai/codex/releases/tag/rust-v0.157.1).
Use a separate tool directory so installing it does not replace your global CLI or Desktop app:

```powershell
$thothCodexPrefix = Join-Path $env:LOCALAPPDATA 'THOTH\tools\codex'
npm.cmd install --prefix $thothCodexPrefix --install-strategy=nested --save-exact @openai/codex@0.157.1
$env:THOTH_CODEX_PACKAGE_ROOT = Join-Path $thothCodexPrefix 'node_modules\@openai\codex'
.\.venv\Scripts\python.exe -m thoth.cli serve --port 8765
```

The package-root value identifies the official wrapper package; it is not an `auth.json` path.
THOTH resolves and checks its native Windows executable, package/version metadata and integrity,
then pins that identity to the selected workspace. An npm shim or a Desktop-bundled executable is
not the model broker. Keep the selected package in place. Version or identity changes require a
reviewed update rather than a silent fallback to another installation.
After setup, the same workspace reuses the pinned package location when the environment variable
is absent; a new terminal does not need to rediscover a different global Codex installation.

These commands install the optional CLI package; they do not log in or call a model. Start the
login from THOTH and use the same workspace for subsequent connection commands. The reviewed
native package target is Windows x64; a working API-key route on another platform is not proof
that this pinned Codex package works there. The isolated nested installation, lock integrity and
native version check passed; product authentication and integrated acceptance are separate in
[the verification record](VERIFICATION.md).

### Optional Claude Code prerequisite on Windows

THOTH looks for Claude Code in its own tools folder first, then `claude` on PATH (an npm shim is
followed to its native `claude.exe`), then the official installer folder. A global Claude Code that
other programs use therefore never overrides the copy THOTH installed. To install a THOTH-only copy
without changing that global one:

```powershell
$thothClaudePrefix = Join-Path $env:LOCALAPPDATA 'THOTH\tools\claude-code'
npm.cmd install --prefix $thothClaudePrefix --install-strategy=nested --save-exact @anthropic-ai/claude-code@2.1.284
```

This does not sign in or call a model. Start the sign-in from the Claude card; it opens the official
browser flow and, if the executable asks for a pasted code, the card forwards it to that process.

## Data location and restarting

`thoth workspace` shows the selected directory without opening the database or moving files.
The local default is `%LOCALAPPDATA%\THOTH` on Windows and `~/.thoth` on macOS/Linux/WSL.
The Windows and WSL defaults are different directories. An explicit `--workspace` always wins.
Starting from a different ZIP extraction or current directory therefore retains the same default
workspace within the same operating-system user environment.

Earlier instructions used `.thoth-local` relative to the source folder. To keep that research,
use its original absolute path for every command, for example:

```powershell
.\.venv\Scripts\python.exe -m thoth.cli workspace --workspace "C:\Research\thoth-old\.thoth-local"
.\.venv\Scripts\python.exe -m thoth.cli serve --workspace "C:\Research\thoth-old\.thoth-local" --port 8765
```

Startup reports the chosen root and nearby legacy directories. Legacy detection only checks the
current directory; it cannot discover every older ZIP extraction. Nothing is automatically moved
or merged. An empty project list can mean a different workspace was selected.

| Stored item | What restarting preserves |
| --- | --- |
| Projects, sources, evidence, saved results and model preferences | Stored under the workspace. Reopen the same root after restarting the backend. A lost model connection does not hide existing local research. |
| Last selected project/thread and unsent draft | Browser local storage, separated by workspace. Use the same browser profile and origin, including its port. Clearing browser data, private browsing or changing origin can remove or separate these preferences. |
| Work still running when the backend stops | A persisted request or queue entry is not proof of a finished answer. Follow the recorded recovery state; restarting does not automatically submit the draft or rerun the provider. |

`workspace-setup.json` is absent only on a new workspace. If an existing setup file is malformed
or cannot be read, `workspace/ready` reports `setup_status: CORRUPT` or `UNREADABLE`, leaves
`execution_ready` false and keeps saved project/research reads available. It does not turn that
file into a new consent decision, grant Internet access, or overwrite the original. A setup
update is refused until the file is recovered. Preserve its bytes and compare a known-good cold
backup before any manual repair; do not delete it merely to clear the warning. Setup writes use
a same-directory temporary file, flush/fsync and atomic replacement. That protects a normally
interrupted write but does not prove durability across a physical power cut.

If the server accepted a question but the response was lost, the local browser keeps the original
request key and submitted payload. Reopening does not send it. Use the explicit recovery action
to check or replay that same request before sending a different one; a newly edited draft stays
separate. Corrupt or unwritable recovery storage blocks a new submission instead of silently
creating another operation. This browser state is also tied to the same profile and origin.

Unsent drafts are convenience storage, not a research record or backup. Drafts over 20,000
characters and browser storage failures show a warning; content is not silently truncated or sent.
Restored project/thread selections are checked against the server before being displayed.

Use a new workspace for first-run smoke tests. Back up an existing workspace before upgrading;
opening it may run database migrations. Stop its backend before copying the whole directory so
the database and stored objects stay together. Synthetic restart tests do not establish resilience
to sudden power loss, and neither browser storage nor a local workspace syncs to another computer.

For a cold backup or move: stop the backend normally, copy the **whole selected workspace**
(`db/thoth.sqlite3`, `objects/sha256`, `workspace-setup.json`, and any workspace-owned auth
profiles) to a protected location, then reopen the copied directory with an explicit
`--workspace` path and read a saved project/result and original evidence before relying on it.
The auth profiles contain secrets; protect or omit them deliberately rather than treating a
research export as a credential backup. Omitting them may require a new login, but does not erase
saved research. Copying only the SQLite file loses referenced original objects. A missing object
or changed object digest is an error to recover from a known-good backup, not a reason to fabricate
bytes or rerun the provider. An active database copy is not a consistent cold backup. If the path
changes, `workspace_id` changes and browser drafts/selections stored under the old origin/root do
not automatically merge; preserve browser storage separately when needed.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_research_followup_read.py -q
```

`pwsh -NoProfile -File .\Makefile.ps1 verify` is the unchanged repository-wide check, a different
scope from feature acceptance. Read [verification status](VERIFICATION.md) before interpreting
failures or running the full suite. Candidate clean-install evidence is recorded there separately.

The clean Windows candidate check passed with Python 3.13.15, Node 24.19.0, pnpm 12.6.0 and uv 0.12.5.
The Web package explicitly includes `@types/node=22.20.4` as a development dependency; the original
clean-install discovery was preserved and fixed without disabling type checking.

# Source installation and local operation

This preview targets a Windows source checkout with Python 3.12 or 3.13. Keep the root uv lock,
pnpm lock/workspace, migration tree and protocol schemas together.

```powershell
uv sync --frozen --extra dev --dev
pnpm install --frozen-lockfile
.\.venv\Scripts\thoth.exe doctor --workspace .\.thoth-local
```

The development extra supplies pytest, Ruff and BasedPyright; the development group includes
reportlab for fixtures. Optional extras are separate:

- `structured-pdf`: Docling; its model assets and resource requirements are separate.
- `browser`: Playwright-based acquisition; browser installation is separate.
- `connectors`: PostgreSQL, S3 and MCP SDKs.
- `managed`: managed sandbox SDK.

The basic UI does not require enabling every optional adapter. An unavailable adapter remains
unavailable until it is deliberately configured.

Run the API and Vite in two terminals as shown in the README. For a production Web build:

```powershell
pnpm --dir apps/web run build
```

`apps/web/dist` is generated output. Desktop/backend-served UI modes require that build; the API
and Vite development interface can run separately.

After building, the local backend also serves the built UI on its own origin. The isolated smoke
used port 18765 to avoid an existing server and confirmed its HTML, JavaScript asset and first-run
screen. No existing workspace is needed for that check.

## First run and credentials

Use THOTH's setup/settings flow. The supported OpenAI account route starts the official Codex
device-auth flow and observes local CLI login status separately. CLI availability and account
entitlement are external prerequisites. Providers without an implemented account-login route
use their supported API-key route.

Keys and model preferences belong to the selected workspace. The default route does not import
OMO credentials automatically. The optional official Codex catalog may read that CLI's local
configuration/cache when present. A configured route is not proof of remote credential validity.

No account configuration is distributed here. Keep `.thoth-local` and credentials out of Git.
Use a new workspace for first-run smoke tests; opening an existing workspace may run migrations.
Synthetic migration tests are not validation of a migration against your own data.

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

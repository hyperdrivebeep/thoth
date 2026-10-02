# Product verification policy

User decision, 2026-10-02: before merge or public release, run one product check on a clean
source checkout and report its results. The user approves the merge after reviewing those results.
During development, retain the focused checks specified in AGENTS.md.

The product check consists of:

- Locked Python and Web installation and the README quick start, through the first setup screen.
- `python -m pytest`: all applicable product, contract, integration and structure tests.
- `pnpm --dir apps/web run test --run`, `lint`, `typecheck` and `build`.
- `python -m ruff check src apps tests migrations conftest.py scripts/pytest_environment.py`.
- `python scripts/check_architecture.py` for runtime layer boundaries. Module/function budgets,
  protocol parity, canonical owners and migration guards remain active in the Python suite.

For the full Python product suite, install the locked `browser` extra in addition to `dev`.
Set `PLAYWRIGHT_BROWSERS_PATH` to an absolute checkout-local `.thoth-test-browsers` path and run
`python -m playwright install --only-shell chromium`. These are test dependencies and browser
assets; no account, external page or model call is required. The basic README first-run screen
still needs only its documented minimal dependencies. See INSTALL.md for the optional setup.

Python lint uses the same product paths as the existing `Makefile.ps1` lint task.
Inactive checkpoint and verification-runner scripts are not runtime product lint targets.

`config/inactive-development-hook-tests.json` identifies exact inactive development diagnostics.
The existing helper excludes them only when repository `.codex/hooks.json` has an empty hooks
object. It reports deselection counts. `--include-inactive-hooks` explicitly includes diagnostics;
nonempty Hook configuration restores them. Invalid selection policy fails visibly.
Private Wiki and process records are local-only, not prerequisites for a public checkout.
Synthetic corruption and authorization guards remain active where they need no private records
or inactive development entry points.

Failures, interruptions, skips, expected failures and unrun checks are reported separately.
A product pass does not certify real provider accounts, answer quality, hosted deployment,
field effectiveness or resolved atomicity debt. No credentials or real model calls are needed.

The older `FULL`/`TOOLING` receipt formats and checkpoint tools are historical development
protocols. Their artifacts are not rewritten or presented as product-check results. They remain
available for explicit diagnostics and are no longer the merge/publication gate.

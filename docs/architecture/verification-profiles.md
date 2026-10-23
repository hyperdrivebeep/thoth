# Product verification policy

User decision, 2026-10-02: before merge or public release, run one product check on a clean
source checkout and report its results. The user approves the merge after reviewing those results.
During development, retain the focused checks specified in AGENTS.md.

The Product check workflow provides this check on clean Windows runners for pull requests,
main pushes, manual dispatches and the nightly default-branch run (18:00 UTC / 03:00 KST).
The existing `checks` job keeps the static, Web and quick-start checks. `python-fast` collects
everything except `tests/integration`; eight `python-integration` matrix jobs cover every
integration test file exactly once. Each uses `-q -rfE -p no:cacheprovider` plus a JUnit report,
with the same root conftest and inactive-Hook selection policy as a local full run.

Before merging, require both `checks` and `python-summary` to pass for the reviewed revision.
The summary requires every Python job to succeed and every report to exist; it shows failures,
errors and skips separately. A complete CI result can supply the full product check without
another mandatory local FULL run. Missing, failed or unverified CI coverage does not waive it.
The first rollout must be checked on a real PR before relying on it. Branch-protection settings
are managed separately; editing this workflow does not configure required checks on the server.

`python scripts/ci_test_shards.py plan` shows the deterministic file assignment and estimated
times. Refresh `tests/ci/integration_durations.json` with
`python scripts/ci_test_shards.py refresh --junit test-results/python.xml` from a complete run.
Only relative file paths and seconds are published. New files join the lightest group using
the median measured file time; stale timing entries do not remove or add tests. The union and
disjointness checks run whenever a shard is selected. Timings are estimates, not timeout changes.

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

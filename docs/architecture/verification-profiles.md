# Product verification policy

User decision, 2026-10-02: before merge or public release, run one product check on a clean
source checkout and report its results. The user approves the merge after reviewing those results.
During development, retain the focused checks specified in AGENTS.md.

The Product check workflow provides this check on clean Windows runners for pull requests,
main pushes, manual dispatches and the nightly default-branch run (18:00 UTC / 03:00 KST).
The existing `checks` job keeps the static, Web and quick-start checks. `python-fast` collects
everything except `tests/integration`; sixteen `python-integration` matrix jobs cover every
integration test file exactly once. Each uses `-q -rfE -p no:cacheprovider` plus a JUnit report,
with ordinary pytest collection, identical to a local full run. The remaining tests are not
filtered by a legacy Hook selector.

Pull requests limited to `README.md`, `README.ko.md` and image files under `docs/screenshots/`
or `docs/brand/` use a documentation/public-package check in the existing `checks` job. A
co-changed `SOURCE_MANIFEST.json` is checked there too; changing only the manifest does not
qualify. Contract documentation, AGENTS, source, tests, settings, dependencies and CI changes
always retain the full product check. Unknown or unavailable diff input selects the full check.
Pushes, scheduled runs and manual dispatches also always run the full check.

The `changes` job compares the exact PR base with the tested HEAD, with renames shown as both
paths. The Python test jobs skip only for that explicit documentation-only result; the independent
`python-types` job still runs on every event, retaining its existing enforcement and artifact contract.
`python-summary` still runs, rejects a failed/missing scope decision, and labels the full Python
suite **NOT_RUN** for documentation-only PRs. In full mode it still requires every shard and report.
The first real PR must verify both modes; local tests and YAML inspection are not a remote CI run.
Both `checks` and `python-summary` run even when scope selection fails and reject missing/failed
scope output, so a failed selector does not turn a required job into a successful skip. The selector
and workflow are reviewed repository code, not a security boundary against a PR that rewrites CI.
Reviewers must inspect CI/script changes rather than trust that PR's own documentation-only label.

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
- `python -m ruff check src apps tests migrations`.
- `python scripts/check_architecture.py` for runtime layer boundaries. Module/function budgets,
  protocol parity, canonical owners and migration guards remain active in the Python suite.

For the full Python product suite, install the locked `browser` extra in addition to `dev`.
Set `PLAYWRIGHT_BROWSERS_PATH` to an absolute checkout-local `.thoth-test-browsers` path and run
`python -m playwright install --only-shell chromium`. These are test dependencies and browser
assets; no account, external page or model call is required. The basic README first-run screen
still needs only its documented minimal dependencies. See INSTALL.md for the optional setup.

Python lint uses the same product paths as the existing `Makefile.ps1` lint task.
On 2026-11-01 the user permanently retired the old development Hook/checkpoint/receipt automation.
Its dedicated tests and helpers, inactive-test list, root selector conftest and selection plugin were
removed together. Product and structural checks that shared those files were retained. There is no
`--include-inactive-hooks` option or conditional Hook exclusion now: every remaining test is collected
normally. Private Wiki and process records remain local-only, not public-checkout prerequisites.

Failures, interruptions, skips, expected failures and unrun checks are reported separately.
A product pass does not certify real provider accounts, answer quality, hosted deployment,
field effectiveness or resolved atomicity debt. No credentials or real model calls are needed.

Older `FULL`/`TOOLING` receipts are historical development evidence. Their stored bytes are preserved,
but the retired runner and checkpoint tools are no longer supported entry points. Neither a historic
receipt nor a focused pass replaces the current product check. Host application approval policies are
outside this repository change.

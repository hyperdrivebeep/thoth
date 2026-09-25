# Verification profile policy — A11

2026-09-14 explicit user decision: inactive development Hook tests are excluded from the default
active-environment suite. The reviewed exact list is `config/inactive-development-hook-tests.json`
(108 historical cases). `conftest.py` / `scripts.pytest_environment.py` deselect only those node IDs
when repository `.codex/hooks.json` explicitly contains an empty hooks object. All other architecture
and product tests and architecture7 stay active. The summary reports deselected counts; historical
failures are not relabelled PASS. Nonempty Hook configuration restores all cases; explicit
`--include-inactive-hooks` also retains the diagnostics. Invalid/missing selection policy fails
visibly. The policy/helper/manifest/Hook configuration are verification-control paths, so changes
require FULL and invalidate older profile baselines. FULL now means all applicable tests for the
recorded runtime configuration; it does not certify disabled Hook implementations.

An incompatible historical proof remains invalid. Read-only running-status observation reports
`prior_verification_state=INVALID_OR_INCOMPATIBLE` without hiding valid live telemetry or marking
the run sealed. This separation does not alter verification/baseline validation.

Decision 2026-09-10: user authorized two execution profiles. This changes development verification
scope only, not product semantics, maturity, owner debt or test assertions. It supersedes unconditional
full-product verification for future eligible tool-only edits after this policy is fully verified.
Until bootstrap FULL completion, the existing full verification requirement remains applicable.

FULL is mandatory for product src/apps, migrations, schemas, dependencies/locks, pytest configuration,
shared fixtures/conftest, unclassified paths, removals/renames, integration acceptance, missing or
incompatible baseline, and verification selection/evidence policy changes. It preserves existing
architecture7, lint/type, all backend, Web lint/type/tests/build and doctor in one completion run.

TOOLING uses only these exact existing developer paths: .codex/hooks/pre_tool_policy.py,
.codex/hooks/stop_acceptance_gate.py, .codex/hooks/session_start.py, scripts/hook_owner_contract.py,
scripts/read_only_command_contract.py, scripts/python_read_contract.py, scripts/parse_shell_units.ps1,
scripts/verification_status.py, scripts/pytest_progress.py and scripts/stop_progress_contract.py;
test_*.py immediately within tests/architecture may be added/modified. Shared helpers are FULL.
Companion Markdown in docs/verification, docs/plans, PROJECT_WIKI and research-briefs is allowed only
outside the hash-bound rule document set. All changed/new/untracked/missing paths remain in the diff.
TOOLING runs architecture7, the existing lint/type checks and all tests/architecture. Empty selection,
skipped/non-passing cases or missing required baseline architecture cases prevents completion;
CLI flags cannot request shrink. Python/pytest/shell/autoload runtime changes require FULL, and
external PYTEST_ADDOPTS/PYTEST_PLUGINS cannot silently override the sealed selection.

The selector, profile evidence contract, completion/Makefile, source/receipt identity, architecture
check selector, this policy, AGENTS, acceptance/maturity contracts and the canonical research design
are policy-bearing. Their bytes and versioned policy data must
match a valid compatible FULL baseline before TOOLING is eligible. Policy changes always run FULL,
under existing trusted candidate validators and independent owner/source/portable regressions.
Selection compares actual current file manifests with the latest valid FULL baseline, not HEAD diff
or declared preflight scope. A partial receipt is never a FULL baseline. Corrupt evidence cannot
authorize shrink; source/index/HEAD/rules drift during a run prevents all completion publication.

New receipts bind profile, baseline, changed files, selected checks/tests, collection/results, exit,
source/rules/runtime and full_suite_verified. TOOLING uses a distinct portable proof/run/terminal
state, full_suite_verified=false and current_source_verified=false. current_scope_verified may be
true only for valid matching TOOLING proof. Stop accepts the owning task's scoped completion without
assigning unrelated work; current-source FULL consumers and Git checkpoint do not accept it as FULL.
Legacy FULL receipt fields and hashes stay unchanged. No trust-store editing or new scheduler.

Read-only explanations need no product verification. Ordinary document-only edits receive applicable
link/format/consistency review without invoking product completion. AGENTS/execution policy/schema/
rule documents are not ordinary documents. Existing single-writer/preflight/Wiki/authority rules stay.

After an owned completion, an ordinary document-only delta can release Stop for document review
without executing product verification. Prior preflight/receipt/portable/Wiki archive identities
must still validate. No receipt is rewritten or new PASS issued; current_source_verified remains
false for the changed source. Mixed product or policy changes retain the normal gate. This is not
a third executable verification profile. New profile-aware source cannot be relabelled as legacy
FULL by stripping its profile metadata. Planned selection uses requires_full_suite, while only
an actual completed verification receipt claims full_suite_verified.

Implementation and RED/GREEN details: ../plans/THOTH_VERIFICATION_PROFILES_PLAN_20260910.md.

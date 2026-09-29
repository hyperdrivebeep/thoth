# MIT public source preview

Repository: [hyperdrivebeep/thoth](https://github.com/hyperdrivebeep/thoth)
First source release: 2026-09-25. License: MIT, copyright 2026 THOTH.

This repository publishes a reviewed source-only snapshot with fresh Git history. The original
private development repository and hosted service are separate from this release.

The package excludes private Git history, user workspaces, credentials, QA databases and
rights-unconfirmed optional assets. Real QA04 data was replaced with synthetic fixtures.

## 2026-09-29 authentication and restart source update

This experimental update includes isolated THOTH-owned provider profiles, explicit API-key/OAuth
selection, login cancellation and refresh guards, and local setup/research recovery behavior.
It also includes the accumulated public-package and regression-fixture repairs since the
2026-09-26 source release. It is published for inspection and local testing before live acceptance.

Scoped backend/Web checks and the final Web build passed on their recorded source stages.
Independent Chrome/RPC checks with synthetic providers accepted a partial scope, including
saved-result reopening in a separate process. A QA diagnostic database-lock failure and
unrun scenarios are preserved in the [verification record](docs/VERIFICATION.md).

**No current-source FULL pass or successful real-provider analysis is claimed.** The latest
completed prior backend FULL recorded 1,694 PASS, 41 FAIL and one SKIP; Web/doctor were not reached.
The older 25-case selected ledger is historical and is not the current failure count.
Real OAuth/provider acceptance, Codex Desktop coexistence, physical Windows reboot, hosted
readiness and collaborator field-effect evidence remain unverified. Claude subscription login
is disabled without explicit client configuration. A fresh Codex login still needs the user's
own first-party authentication and any required MFA.

GitHub source publication does not deploy or update the hosted service.

## Earlier source release

The 2026-09-26 Codex first-run repair had focused local and Chrome verification recorded
in [docs/VERIFICATION.md](docs/VERIFICATION.md). Repository-wide pytest/type checks were RED on
that earlier package's absent `apps.api` test imports; the current update restores the package
and records later checks separately rather than reclassifying that historical failure.
The merged source was downloaded again as GitHub's `main.zip`; manifest hashes, frozen dependency
sync, Web build and doctor passed. ZIP extraction creates `thoth-main`, which the quick-start now
identifies.

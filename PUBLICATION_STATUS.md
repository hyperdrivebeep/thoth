# MIT public source preview

Repository: [hyperdrivebeep/thoth](https://github.com/hyperdrivebeep/thoth)
First source release: 2026-09-25. License: MIT, copyright 2026 THOTH.

This repository publishes a reviewed source-only snapshot with fresh Git history. The original
private development repository and hosted service are separate from this release.

The package excludes private Git history, user workspaces, credentials, QA databases and
rights-unconfirmed optional assets. Real QA04 data was replaced with synthetic fixtures.

Focused checks and initial UI/API smoke passed; 25 known source-tree regression cases remain
separate. No full current-source regression, live-provider quality, hosted readiness or actual
collaborator field-effect claim is made. See [verification](docs/VERIFICATION.md).

GitHub source publication does not deploy or update the hosted service.

The 2026-09-26 Codex first-run repair has focused local and Chrome verification recorded
in [docs/VERIFICATION.md](docs/VERIFICATION.md). Repository-wide pytest/type checks remain RED on
the public package's absent `apps.api` test imports; no full current-source pass is claimed.

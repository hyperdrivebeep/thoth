# Source-only package boundary

The preview contains selected code, migrations, schemas, build/test configuration, developer rules,
public documentation and three self-described synthetic example packs. It contains no Git history,
local workspaces, credentials, user databases, deployment configuration, verification dumps,
screenshots/reference images, dependency installations or generated Web bundle.

Original QA04 Web fixtures were replaced in this public copy with synthetic data while preserving
the schema, 149 selected refs, 50/50/49 paging and 25 available citations. The private source files
were preserved. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

Some legacy tests require optional ProjectPacks or local-only proof files not distributed here.
Those test sources and the full verification command remain visible; their absence is not hidden
by new skips or weaker assertions. The recorded 25 failures describe a selected run on the original
development tree, not a claim that every other test passes in this export.

The public NOW page is a concise status projection. Original internal timelines, task identifiers,
machine paths and private run artifacts are not included. Historical architecture reference pages
must be read with the current [verification limits](VERIFICATION.md).

The bounded clean-install/content review is complete and MIT is applied to the reviewed source
snapshot. The source preview is published in the separate `thoth-public` repository with fresh
Git history. The private development repository and hosted service are not part of this release.

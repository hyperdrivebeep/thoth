# Verification and known limits

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

Follow-up selected checks resolved 61 of those original failing nodes; 25 remain unresolved.
This is a disposition across scoped receipts, not a full rerun of the current repository. No
current-source global TYPE/FULL pass is claimed. Exact remaining nodes are in
[KNOWN_TEST_FAILURES.json](KNOWN_TEST_FAILURES.json).

Remaining cases concern source-time prerequisites, export/restore boundaries, Hero/sandbox paths,
evaluation/currentness and other display/contract mismatches. Some causes remain uncertain.
Assertions, selectors and time budgets were not relaxed to portray a passing full suite.

Canonical-owner atomicity debt remains open. Focused transaction evidence does not establish
complete atomicity for all owners or execution paths.

## Candidate checks

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

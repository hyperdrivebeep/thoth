# Publication content review

This source-only preview was assembled from an explicit file selection. Git history, account
state, research databases, private diagnostics, deployment files and rights-unconfirmed legacy
assets are outside the package. Original working files were preserved.

The original three QA04 Web JSON fixtures contained real QA output and external source excerpts.
They are not distributed. Their replacements are a fictional greenhouse-sensor case with newly
generated identities, source text, locations and digests. The fixture contract still has 149
selected references, 50/50/49 paging and 25 available cited spans.

Gitleaks 8.30.1 scanned the selected directory using built-in rules with redacted reporting.
It returned three generic-key findings. Source review classified all three as false positives:
one Python import identifier and two synthetic RPC idempotency request labels. No credential value
was confirmed. The raw scanner exit was 1; it was not rewritten as an automated zero-findings pass.
No blanket secret-scan suppression was added to the public source.

Location/email review found test fixtures using reserved example domains, deliberately invalid
URL userinfo, and synthetic private local paths. No developer-home path or real account file is
included. This is a bounded content review, not a guarantee that all security defects are absent.

Installed dependencies and generated binary/Web output are not part of this source archive.
Redistribution notices and optional-asset omissions are described in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) and [PACKAGING.md](PACKAGING.md).

Clean candidate installation, doctor, 36 focused Web tests, corrected Web build and isolated
first-run Chrome/API smoke passed. See [VERIFICATION.md](VERIFICATION.md). The owner subsequently
selected MIT; only license notices and package license metadata changed. Runtime code, tests,
dependency selections and lockfiles were unchanged. GitHub source publication was subsequently
authorized on 2026-09-25. Publication updates only these status documents and the source manifest;
the reviewed runtime, test and dependency files remain unchanged. Private Git history is excluded.

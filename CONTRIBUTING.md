# Contributing

Start with [installation](docs/INSTALL.md) and [known limits](docs/VERIFICATION.md).
Reproduce bugs in a synthetic project and an isolated workspace. Share the command, expected and
observed behavior, and relevant version. Omit keys, private documents and local databases from
issue attachments.

Keep domain rules separate from I/O adapters. Use existing registries/factories for new provider,
connector, parser and sandbox variants. Preserve authorization, source time/currentness,
idempotency and explicit partial/held outcomes.

Run focused checks for changed behavior. A fault test must reach its intended fault boundary.
When updating an outdated fixture, preserve the original failure and its relevant negative case.
Do not drop assertions, widen permissions, raise budgets or add skips merely to make checks green.

This source snapshot uses the [MIT License](LICENSE). Third-party notices remain separate.
The owner will choose the final repository destination before publication. This candidate
does not deploy a service or publish itself.

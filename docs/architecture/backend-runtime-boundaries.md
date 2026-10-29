# THOTH backend runtime boundaries

This is the authoritative runtime-boundary rule for the local modular monolith. It adapts the Academy AX invariants to an application-centered Python system; it does not move THOTH business rules into SQLite.

## Ownership

| Boundary | Owns | Must not own |
|---|---|---|
| `domain/` | typed aggregates, invariants, canonical state vocabulary | adapters, persistence, model/provider calls, HTTP/CLI |
| `ports/` | abstract storage, model, tool, clock, ID, registry and Unit-of-Work contracts | concrete libraries or policy decisions |
| `application/` | use cases, policy/authority orchestration, deterministic reducers, transaction intent | concrete adapters, provider selection, file/network/process I/O |
| `adapters/` | SQLite/object storage, parsers, models, connectors, sandboxes, HTTP/CLI translation | canonical product decisions or duplicate state machines |
| `apps/` | composition roots, extension registration and runtime lifecycle | domain rules |
| canonical ledger | immutable domain revisions, heads, event/receipt lineage | derived search/vector/summary views |
| specialized projections | transactionally updated current/query views | independent canonical truth |

## Canonical owner rule

Every aggregate has exactly one canonical owner in `config/architecture-conformance.json`. A specialized table may be a current projection, but it must be derived from or transactionally committed with the canonical revision. `ControlRecord` is not a universal escape hatch for canonical state.

When canonical ledger and projection disagree, the command fails closed and reconciliation is required. Reads that drive a protected decision use the canonical revision or a projection whose digest is bound to it.

## Unit of Work

One accepted command owns one atomic Unit of Work:

```text
validate project/actor/policy/cutoff/head
→ stage canonical revision and projection
→ update head/dependency state
→ append audit event
→ seal receipt
→ commit once
```

Failure before commit leaves none of those state changes visible. Content-addressed bytes written before the database transaction may remain as unreferenced immutable blobs; they are not canonical until the database reference commits and may be garbage-collected by a separate verified process.

## Idempotency and concurrency

- Every mutation carries a project-scoped idempotency key and normalized scope digest.
- Reusing the key with the same scope returns the original terminal result.
- Reusing it with a different scope returns a typed conflict.
- Every update validates expected revision/head/checkpoint inside the same transaction.
- Last-write-wins is prohibited for domain state.

## Migration discipline

- Production and normal local runtime schema are created by immutable Alembic migrations.
- Fresh and upgrade replay must both pass.
- `metadata.create_all()` is prohibited in normal runtime and ledger initialization. The app
  composition runs the typed Alembic migration port to `head`; the ledger then verifies the stamped
  single-head schema and fails closed when it is absent or inconsistent.
- A schema change updates migration, typed contract, storage adapter and tests in the same change.

## Errors and responses

Domain failures use stable reason codes. Protocol errors, domain HOLD/ABSTAIN, and internal failures are distinct. Public responses expose only safe messages and identifiers. Clients never branch on Korean or English prose.

## External effects

R0-R2 stay reversible/read-only/isolated as defined by policy. R3 uses an exact-digest authorization, final preflight, idempotent dispatch and ambiguous-result reconciliation. R4 has no executor. No connector or sandbox writes canonical state directly.

## Optional derived embedding index

The embedding index is a project-local, rebuildable projection, never a source of research truth.
Normal runtime construction has no embedding worker and project indexing defaults to disabled.
An explicitly injected `BoundedEmbeddingWorkerPort` exposes the managed `embedding_index` service;
this capability does not change retrieval policy or permit CANARY exposure. Model installation,
ORT execution and UI activation are separate integration stages.

Source publication stages only an immutable intent in the existing SQLite UoW. This includes
connector acquisition and the shared artifact publication used by upload, ProjectPack and sandbox.
Encoding is explicitly drained after commit, outside any ambient transaction. Each checkpoint
uses a short transaction and a generation/owner lease. READY is a compare-and-swap publication of
the complete manifest after checking current source identity, active binding, configuration,
individual span access, text hashes and every immutable vector. Readers recheck current scope and
manifest integrity; partial staging is never a ready index.

Content keys include the text SHA, pinned model/revision and preprocessing contract, with project
namespaces and separate source links. Changed, removed or detached sources cannot be read through
an old READY manifest. Explicit reconciliation invalidates obsolete links and stages missing current
ones. Expired leases become FAILED; there is no automatic retry. A generation-fenced manual retry
can reuse validated partial blobs. Orphan collection only removes unreferenced derived vectors.
Export/ProjectPack continue to carry canonical source data, not these tables.

The typed `EmbeddingBudget` bounds one batch-1 worker, query wait plus encoding to 2 seconds,
each span to 10 seconds, a job and one cumulative project drain to 10 minutes, leases to 60 seconds,
and queued queries to 16. A drain passes its remaining deadline to each source job; starting the
next source cannot reset the request budget.
Foreground queries run before waiting background spans. Timeout and cancellation stop the owned
process tree and check termination. Windows uses a Job Object with a 512 MiB committed-memory cap;
this is not an RSS measurement or proof of retrieval latency/quality. Runtime close and failed
startup release the optional worker. The child factory is trusted application configuration;
Python socket denial is a defensive guard, not an operating-system sandbox for untrusted code.

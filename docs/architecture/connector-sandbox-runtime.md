# Connector and Sandbox Runtime

## Purpose

Connector and Sandbox are replaceable integration ports beneath the THOTH evidence-hypothesis-action-outcome loop. They do not replace source authority, sufficiency, action policy, semantic revision or human R3/R4 boundaries.

## Connector

`ConnectorPort` exposes capability, discover, fetch and close. `ConnectorService` performs policy preflight before driver I/O, persists a versioned `CONNECTOR/RUN`, ingests fetched bytes through the existing parser/object/evidence pipeline, and seals a `CONNECTOR/RECEIPT` with `semantic_truth_certified=false`.

Automatic routing is based on selector shape plus registered `source_kind`, then restricted by the environment connector allowlist:

```text
relative_path                              -> LOCAL
repository_path + revision + file_path    -> GIT
view (+ bounded integer limit)            -> POSTGRES
bucket + key                              -> S3
uri                                       -> MCP
```

The router never accepts raw password, token, API key, private key or DSN fields. Credentials remain adapter-owned through OS sessions, credential helpers, DB roles, IAM/OIDC or MCP OAuth.

`--connector-config` loads a strict YAML registry. LOCAL/GIT roots are explicit, PostgreSQL uses `dsn_env`, S3 uses the default credential chain, and MCP server URLs must be HTTPS or loopback HTTP. The environment profile allowlist still decides which configured connector IDs may run.

Reference adapters:

- `LocalFileConnector`: bounded root, native file identity, size and content hash.
- `GitReadConnector`: verified commit object and `git show`, independent from worktree drift.
- `PostgresReadConnector`: approved view only, adapter-generated SELECT, bounded integer limit, read-only transaction and statement timeout. Raw SQL from the agent is rejected.
- `S3ReadConnector`: approved bucket/prefix, VersionId/ETag and Boto3 default credential chain.
- `McpResourceConnector`: URI prefix scope and official MCP v2 Client bridge.

## Sandbox

`SandboxRunSpec` binds one project and execution attempt to an immutable image, argv array, read-only artifact snapshots, deny-by-default network, resource limits and policy digest. V1 forbids secret injection.

Adapters:

- `DisabledSandboxAdapter`: fail-closed default.
- `ScriptedSandboxAdapter`: deterministic state-machine and workflow QA.
- `DockerSandboxAdapter`: local Linux POC; no network, read-only rootfs, non-root user, all capabilities dropped, no-new-privileges, PID/CPU/memory/time/output limits and forced cleanup.
- `GVisorSandboxAdapter`: same contract with registered `runsc` runtime for Linux staging.
- `FirecrackerSandboxAdapter`: digest-bound, networkless fixed microVM bundle with read-only rootfs, guest success marker, bounded serial output and forced VMM cleanup.
- `E2BManagedSandboxAdapter`: managed E2B template with public internet disabled, no external inputs or secrets in V1, bounded command/output and forced sandbox kill.

The trusted `SandboxService`, not sandboxed code, verifies project input bindings and hashes. After execution it seals a sandbox receipt, ingests a bounded JSON result as an informal project artifact, and links the resulting evidence spans to the existing Execution attempt. `SUCCEEDED` remains process success, not Outcome validity or approval.

## Activation

Scripted runtime is the deterministic contract baseline. Docker, Linux gVisor, Firecracker and E2B managed all pass live THOTH R2 cycles through the same SandboxPort contract. E2B public internet is disabled and the sandbox is killed after result collection. Real-institution credentials and institution-specific accreditation remain outside the build until a pilot exists.

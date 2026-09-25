# Extension point catalog

| Extension | Port/contract | Registration owner | Core must remain closed to |
|---|---|---|---|
| Model | `ModelPort`, `ModelResolverPort` | `apps.model_composition.create_models` registers providers and wraps current resource checks; consumed by runtime | provider names and credential logic |
| Parser | `ParserPort`, `ParserRegistryPort`, optional capability contract and `AsyncParserRegistryPort` | `ParserRegistry` in adapter composition; shared by source connection and ProjectPack ingestion | file-format/vendor-specific branches and implicit structured-parser fallback |
| Connector | `ConnectorPort`, `ConnectorRegistryPort` | `ConnectorRegistry` in adapter composition | LOCAL/GIT/S3/DB/MCP names |
| Sandbox | `SandboxPort`, `SandboxFactoryRegistryPort` | `SandboxFactoryRegistry` in adapter composition | concrete adapter class names |
| Store | `StoreBundlePort`, `StoreFactoryPort`, `StoreFactoryRegistryPort` + shared managed ledger/UoW | `default_store_factory_registry` → `apps.storage_composition.open_stores`; SQLite construction/migration in its bundle factory | provider-specific branches and independently constructed DB participants in application composition; owner-wide atomicity remains separately PARTIAL |
| Criterion profile | versioned profile contract + router port | `apps.thread_analysis_composition.create_thread_analysis` | project/domain-name branches |
| ProjectPack loader | `ProjectPackLoaderPort` | `FilesystemProjectPackLoader` registration | filesystem paths and loader implementation |
| ProjectPack execution | `ProjectPackExecutionFactoryPort` + `ModelResolverPort` | app composition | provider names, concrete models and runtime composition |
| Schema migration | `SchemaMigrationPort` | `AlembicSchemaMigrator` | `create_all`, implicit schema repair and multiple heads |
| Evaluator | evaluator contract + exposure ledger | evaluation registry | hidden-holdout content |
| Evaluation executor | `EvaluationExecutionPort`, `EvaluationExecutorRegistryPort` | `default_execution_registry` in Improvement composition | executor identity branches and hidden expected outputs |
| Behavior component | `BehaviorComponentHandlerPort`, `BehaviorComponentRegistryPort` | `default_behavior_components` in behavior runtime composition | provider/fixture names and unrestricted executable code |

N05 supplies an explicitly configured frozen case/scorer catalog and a bounded declarative subprocess
executor. Separate workspaces receive only public inputs and independent initial memory copies.
The scorer consumes expected values separately. Absent configuration produces HOLD; the legacy fixed
evaluator is TEST_ONLY. This extension does not execute arbitrary Python or activate runtime behavior.

Adding an extension may add a factory registration in the composition root. It must not require editing existing application decision logic unless the accepted semantic contract changes.

## Structured source registration (2026-09-16)

The W0–W7 implementation registers the optional local `docling-pdf` adapter alongside the existing
PDF parser. `ParserSelection` selects by name and required capabilities; multiple matching parsers
without an explicit default fail as ambiguous. Advertised capability is separate from observed
structure coverage. The generic ingestion service stages immutable parser input, awaits parsing,
then rechecks access and persists through its existing owner. No SQLite handle enters the parser
worker. `ScopedArtifactLedger.read_structure` supplies source-version-bound nodes and observations
to research context assembly; table semantics are represented by typed relations, not parser names.

The independent JSON structure fixture exercises registration, source connection, structure read,
context expansion and the research/readback path. It does not establish universal PDF correctness.
The dedicated WSL dependency/asset arm, limitations and live acceptance are recorded in
`docs/verification/research-context-implementation-20260915.md`. Existing owner10/FULL status is
unchanged, and this entry does not reactivate archived development hooks.

`config/architecture-conformance.json` names an actual source symbol for every `IMPLEMENTED`
factory target. `PARTIAL` is permitted only with an explicit debt ID and owning Acceptance IDs.
`OCP-REGISTRY-001`, `OCP-FACTORY-001` and `OCP-CORE-CLOSED-001` enforce these conditions.

## Evidence limits and executable binding (2026-09-05)

The manifest now names a fully qualified concrete factory, its existing factory protocol, and its
actual composition function. Exact AST module/export resolution, protocol signature checks and
registration checks reject unrelated same-name symbols and disconnected/missing registrations.
Backend typechecking and representative Model/Connector/Sandbox/Evaluator extension tests remain
required: syntax and signatures alone do not prove that every runtime path uses the registration.

The known-string list is a narrow regression guard. Import/dependency checks, provider/adapter
identity-selection checks, typechecking and representative behavior tests supplement it. They do not
prove OCP for arbitrary Python reflection, dynamically assembled names or every possible new vendor.
Project-local source authorization comparisons are not factory routing; they remain separately
subject to A11/A13 authority tests. No new architecture exception is granted.

Architecture, preflight and completion use `scripts/required_architecture_checks.py`. Required
check identities, PASS/error/exit/skip state and the executable guard bundle are validated. A rule
change requires a replacement preflight; historical archived receipts are preserved, not rewritten.
Completion seals source/index stability across full verification and rechecks actual Wiki bytes.

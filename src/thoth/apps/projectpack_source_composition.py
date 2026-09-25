"""ProjectPack source policy and ingestion use the same ownership boundary as normal runtime."""

from pathlib import Path

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.application.services.ingestion_service import IngestionService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.apps.resource_scope_composition import create_resources
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.governance import ProjectPolicy
from thoth.domain.project import Project
from thoth.domain.projectpack import LoadedProjectPack
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.parser import ParserRegistryPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort


def _pack_policy(pack: LoadedProjectPack, project: Project, clock: ClockPort) -> ProjectPolicy:
    policy_payload: dict[str, object] = {
        "external_write": pack.policy.external_write,
        "physical_action": False,
        "unknown_action_tier": pack.policy.unknown_action_family_tier.value,
        "connector_default": "DENY",
        "connector_allowlist": ["local-file-upload", "read-only-git-snapshot"],
        "connector_allowed_egress_classes": ["NONE"],
        "max_source_security_class": "RESTRICTED",
        "sandbox_runtime_allowlist": [],
        "sandbox_network_policy": "DENY_ALL",
        "sandbox_allowed_hosts": [],
    }
    if pack.policy.resource_scope_policy is not None:
        policy_payload["resource_scope_policy"] = pack.policy.resource_scope_policy.model_dump(
            mode="json"
        )
    return ProjectPolicy(
        policy_id=project.policy_binding_ref,
        project_id=project.project_id,
        version=1,
        payload=policy_payload,
        policy_digest=domain_digest(
            "PROJECT_POLICY",
            "1.0.0",
            canonical_payload(
                {
                    "project_id": project.project_id,
                    "policy": policy_payload,
                }
            ),
        ),
        created_at=clock.now(),
    )


def create_pack_project(
    pack: LoadedProjectPack, stores: StoreBundlePort, clock: ClockPort
) -> Project:
    project = Project(
        project_id=pack.project.project_id,
        name=pack.project.name,
        cutoff_at=pack.project.cutoff_at,
        overlay=pack.project.overlay,
        policy_binding_ref=pack.project.policy_binding_ref,
    )
    policy = _pack_policy(pack, project, clock)
    with stores.ledger.transaction():
        stores.projects.create(project, created_at=clock.now().isoformat())
        stores.governance.put_policy(policy)
    return project


def create_pack_ingestion(
    pack: LoadedProjectPack,
    workspace: Path,
    ledger: ManagedLedgerPort,
    stores: StoreBundlePort,
    project: Project,
    projects: ProjectStorePort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    parsers: ParserRegistryPort | None = None,
) -> tuple[ScopedArtifactLedger, IngestionService]:
    governance = stores.governance
    artifacts = create_resources(ledger, stores, projects, governance, clock, ids)
    ingestion = IngestionService(
        projects=projects,
        objects=stores.objects,
        parsers=parsers or default_parser_registry(),
        artifacts=artifacts,
        clock=clock,
        ids=ids,
        scopes=artifacts.scopes,
    )
    ingestion.prepare_resource_scope(project.project_id)
    return artifacts, ingestion

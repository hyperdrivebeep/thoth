from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import orjson
from pydantic import JsonValue

from thoth.adapters.projectpacks import load_project_pack
from thoth.adapters.projectpacks.loader import source_path
from thoth.adapters.runtime import SystemClock
from thoth.adapters.sandbox import ScriptedSandboxAdapter
from thoth.adapters.storage import SqliteConversationSessionStore, SqliteOperationStore
from thoth.application.services.conversation_router import ConversationRouter
from thoth.application.services.tui_session_service import TuiSessionService
from thoth.apps.conversation_dispatch import BusConversationDispatcher
from thoth.apps.runtime import create_runtime
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import CausalLocus
from thoth.domain.operation import OperationRecord
from thoth.ports.memory import MemoryReviewerPort
from thoth.ports.model import ModelPort, ModelResolverPort
from thoth.ports.sandbox import SandboxPort
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse

HERO_INSTRUCTION = (
    "Analyze the authorized evidence, acquire any configured missing comparison manifest, "
    "challenge the leading high-risk hypothesis, compare the safe action frontier, and execute "
    "only a bounded R2 sandbox replay. Preserve uncertainty and protected-action boundaries."
)

STAGE_ORDER = (
    "source_cutoff",
    "reasoning",
    "acquisition",
    "counter_search",
    "sandbox_outcome",
    "revision_memory_receipt",
    "closure_baseline_r3_preview",
)

_MANUAL_SEMANTIC_METHODS = frozenset(
    {
        "criteria/contract/create",
        "hypothesis/create",
        "hypothesis/portfolio/create",
        "action/create",
        "action/portfolio/create",
        "outcome/series/create",
        "revision/changeset/create",
    }
)


@dataclass(frozen=True)
class HeroRunResult:
    manifest: dict[str, object]
    observations: dict[str, object]


class SingleModelResolver(ModelResolverPort):
    def __init__(self, model: ModelPort) -> None:
        self._model = model

    def resolve(self, *, provider: str, model: str | None = None) -> ModelPort:
        del provider, model
        return self._model


async def _dispatch(
    bus: object,
    method: str,
    key: str,
    value: dict[str, object],
) -> dict[str, JsonValue]:
    from thoth.protocol.bus import CommandBus

    response = await cast(CommandBus, bus).dispatch(
        JsonRpcRequest.model_validate(
            {
                "id": key,
                "method": method,
                "params": {"_meta": {"idempotencyKey": key}, "input": value},
            }
        )
    )
    return _value(response)


def _value(response: JsonRpcResponse) -> dict[str, JsonValue]:
    if response.error is not None:
        raise RuntimeError(
            f"{response.error.code}:{response.error.message}:"
            f"{json.dumps(response.error.data, sort_keys=True)}"
        )
    assert response.result is not None
    child = response.result["value"]
    if not isinstance(child, dict):
        raise TypeError("Hero RPC result must be an object")
    return cast(dict[str, JsonValue], child)


def _policy_payload() -> dict[str, object]:
    all_loci = [item.value for item in CausalLocus]
    return {
        "resource_scope_policy": {
            "default": {"owner_kind": "PROJECT", "visibility": "PROJECT_SHARED"}
        },
        "external_write": False,
        "physical_action": False,
        "unknown_action_tier": "R3",
        "connector_default": "DENY",
        "connector_allowlist": ["local-file-upload"],
        "connector_allowed_egress_classes": ["NONE"],
        "max_source_security_class": "INTERNAL",
        "sandbox_runtime_allowlist": ["SCRIPTED"],
        "sandbox_network_policy": "DENY_ALL",
        "sandbox_allowed_hosts": [],
        "acquisition_routes": [
            {
                "evidence_group": "comparison_configuration_manifest",
                "match_terms": ["source-bound configuration manifest"],
                "connector_id": "local-file-upload",
                "selector": {"relative_path": "authorized-comparison-manifest.md"},
                "query_families": ["configuration lineage", "comparison manifest"],
                "max_waves": 1,
                "max_results": 1,
            }
        ],
        "counter_search_routes": [
            {
                "route_id": "independent-alternative-check",
                "target_loci": all_loci,
                "connector_id": "local-file-upload",
                "selector": {"relative_path": "independent-counter-check.md"},
                "query_families": ["independent counterevidence", "alternative explanation"],
                "source_territory": "independent-method-review",
                "independence_group": "independent-counter-source",
                "alternative_explanation": (
                    "The apparent gap may be a reporting or comparability artifact rather than "
                    "the leading causal explanation."
                ),
                "source_authority": "OFFICIAL",
                "temporal_state": "ELIGIBLE",
                "support_match_terms": ["method mismatch"],
                "counter_match_terms": ["does not confirm", "alternative explanation"],
                "max_waves": 1,
                "max_results": 2,
                "priority": 100,
                "depth": 1,
            }
        ],
        "counter_search_limits": {
            "max_waves": 1,
            "max_results": 2,
            "max_documents": 2,
            "max_bytes": 1048576,
            "max_model_calls": 0,
            "max_tool_calls": 4,
            "max_time_seconds": 60,
            "max_cost_microunits": 10,
            "max_depth": 1,
            "min_independent_confirmations": 1,
            "saturation_voi_threshold": "0.10",
        },
        "sandbox_action_templates": [
            {
                "action_family": "SANDBOX_REPLAY",
                "runtime_profile": "SCRIPTED",
                "image_digest": "scripted:hero-current-core-v1",
                "argv": ["python", "-c", "print('bounded comparison replay')"],
                "network_policy": "DENY_ALL",
                "allowed_hosts": [],
                "resource_limits": {
                    "cpu_millis": 1000,
                    "wall_seconds": 60,
                    "memory_mib": 256,
                    "disk_mib": 256,
                    "pids": 16,
                    "stdout_bytes": 1048576,
                    "stderr_bytes": 1048576,
                },
            }
        ],
        "r2_recovery_policy": {
            "max_transient_retries": 0,
            "transient_markers": ["TRANSIENT_INFRA", "BOOT_FAILED", "TIMED_OUT"],
            "semantic_markers": ["SEMANTIC"],
            "code_markers": ["CODE"],
            "ambiguous_markers": ["AMBIGUOUS_EXTERNAL"],
        },
    }


def default_hero_policy_payload() -> dict[str, object]:
    return _policy_payload()


def _write_supplemental_sources(inbox: Path) -> None:
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "authorized-comparison-manifest.md").write_text(
        "# Authorized comparison manifest\n\n"
        "A source-bound configuration manifest records version, workload, method, and evaluator "
        "lineage. It narrows the comparison gap without certifying scientific truth.\n",
        encoding="utf-8",
    )
    (inbox / "independent-counter-check.md").write_text(
        "# Independent counter-check\n\n"
        "An independent method review does not confirm that a method mismatch alone explains the "
        "gap. An alternative explanation is a reporting or comparability artifact.\n",
        encoding="utf-8",
    )


def _contract_digests(policy_payload: dict[str, object]) -> dict[str, JsonValue]:
    runner_path = Path(__file__).resolve()
    return {
        "runner": hashlib.sha256(runner_path.read_bytes()).hexdigest(),
        "prompt": domain_digest("HERO_PROMPT_CONTRACT", "1.0.0", HERO_INSTRUCTION.encode()),
        "policy_schema": domain_digest(
            "HERO_POLICY_SCHEMA",
            "1.0.0",
            canonical_payload({"fields": sorted(policy_payload)}),
        ),
        "response_schema": domain_digest(
            "HERO_RESPONSE_SCHEMA",
            "1.0.0",
            canonical_payload({"stages": STAGE_ORDER}),
        ),
    }


async def run_current_hero(
    *,
    pack_name: str,
    workspace: Path,
    model: ModelPort | None = None,
    execution_mode: str = "LIVE",
    manifest_path: Path | None = None,
    sandbox_adapter: SandboxPort | None = None,
    policy_payload: dict[str, object] | None = None,
    memory_reviewer: MemoryReviewerPort | None = None,
) -> HeroRunResult:
    pack_root = Path(__file__).resolve().parents[2] / "examples" / "projectpacks" / pack_name
    pack = load_project_pack(pack_root)
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for source in pack.sources:
        shutil.copy2(source_path(pack, source), inbox / source.path)
    _write_supplemental_sources(inbox)
    effective_policy = _policy_payload() if policy_payload is None else policy_payload
    runtime = create_runtime(
        workspace,
        sandbox_adapter=(ScriptedSandboxAdapter() if sandbox_adapter is None else sandbox_adapter),
        model_resolver=(None if model is None else SingleModelResolver(model)),
        memory_reviewer=memory_reviewer,
    )
    project_id = pack.project.project_id
    try:
        await _dispatch(
            runtime.bus,
            "project/create",
            f"hero:{pack_name}:project",
            {
                "project_id": project_id,
                "name": pack.project.name,
                "cutoff_at": pack.project.cutoff_at.isoformat(),
                "overlay": pack.project.overlay,
                "policy_binding_ref": pack.project.policy_binding_ref,
            },
        )
        await _dispatch(
            runtime.bus,
            "project/policy/update",
            f"hero:{pack_name}:policy",
            {
                "project_id": project_id,
                "expected_revision": 0,
                "payload": effective_policy,
            },
        )
        for ordinal, source in enumerate(pack.sources):
            await _dispatch(
                runtime.bus,
                "project/source/connect",
                f"hero:{pack_name}:source:{ordinal}",
                {
                    "project_id": project_id,
                    "connector_id": "local-file-upload",
                    "selector": {"relative_path": source.path},
                    "media_type": source.media_type,
                    "authority": source.authority.value,
                    "cutoff_state": source.cutoff_state.value,
                    "security_class": source.security_class.value,
                    "version_label": source.version_label,
                },
            )
        started = await _dispatch(
            runtime.bus,
            "thread/start",
            f"hero:{pack_name}:thread",
            {
                "project_id": project_id,
                "thread_id": pack.scenario.thread_id,
                "cycle_id": pack.scenario.cycle_id,
                "problem": pack.scenario.problem,
                "scope": {"workstream": "current-hero", "pack_overlay": pack.project.overlay},
            },
        )
        tui = TuiSessionService(
            session_id=f"tui:hero:{pack_name}",
            store=SqliteConversationSessionStore(runtime.ledger.engine),
            router=ConversationRouter(),
            dispatcher=BusConversationDispatcher(runtime.bus),
            clock=SystemClock(),
        )
        analysis_turn = await tui.execute(HERO_INSTRUCTION)
        if analysis_turn.status.value != "DISPATCHED":
            raise RuntimeError(f"Hero TUI normal entry held: {analysis_turn.error_message}")
        analysis = analysis_turn.response
        if analysis.get("status") == "ACCEPTED_RUNNING":
            await runtime.bus.drain()
            operation = runtime.bus.read_operation(str(analysis["operation_id"]))
            if operation is None or operation.error is not None or operation.result is None:
                message = None if operation is None or operation.error is None else operation.error.get("message")
                raise RuntimeError(str(message or "Hero research did not produce a final result"))
            analysis = operation.result
            if analysis.get("terminal_reason") != "BOUNDED_RESEARCH_COMPLETE":
                raise RuntimeError(f"Hero research is partial: {analysis.get('terminal_reason')}")
        evidence = await _dispatch(
            runtime.bus,
            "evidence/list",
            f"hero:{pack_name}:evidence",
            {"project_id": project_id},
        )
        head_result = await _dispatch(
            runtime.bus,
            "revision/head/read",
            f"hero:{pack_name}:heads",
            {"project_id": project_id},
        )
        heads = cast(dict[str, str], head_result["working_heads"])
        aggregate_key, current_head = sorted(heads.items())[0]
        aggregate_id = aggregate_key.split(":", 1)[-1]
        branch = await _dispatch(
            runtime.bus,
            "revision/branch/create",
            f"hero:{pack_name}:branch",
            {
                "project_id": project_id,
                "aggregate_id": aggregate_id,
                "from_revision_digest": current_head,
                "purpose": "preview an independent review branch",
                "actor_or_agent_ref": "agent:hero-preview",
            },
        )
        restore_preview = await _dispatch(
            runtime.bus,
            "revision/restore/preview",
            f"hero:{pack_name}:restore-preview",
            {
                "project_id": project_id,
                "aggregate_id": aggregate_id,
                "target_revision_digest": current_head,
                "current_head_digest": current_head,
            },
        )
        baseline_preview = await _dispatch(
            runtime.bus,
            "revision/baseline/prepare",
            f"hero:{pack_name}:baseline-preview",
            {
                "project_id": project_id,
                "purpose": "preview current Hero milestone baseline",
                "scoped_head_map": heads,
                "policy_version": "baseline:hero-preview-v1",
                "evidence_refs": cast(list[str], analysis["selected_evidence_refs"]),
            },
        )
        assessment = cast(dict[str, JsonValue], analysis["assessment"])
        missing_items = cast(list[str], assessment.get("missing_items", []))
        closure = await _dispatch(
            runtime.bus,
            "closure/prepare",
            f"hero:{pack_name}:closure-preview",
            {
                "project_id": project_id,
                "resolution": "bounded current-core Hero review",
                "unresolved_refs": missing_items,
                "open_effect_refs": [],
            },
        )
        closure_value = cast(dict[str, JsonValue], closure["closure"])
        purge_preview = await _dispatch(
            runtime.bus,
            "closure/purge/prepare",
            f"hero:{pack_name}:r3-preview",
            {
                "project_id": project_id,
                "closure_id": str(closure_value["closure_id"]),
                "exact_scope_refs": [aggregate_id],
                "reason": "protected R3 preview only",
            },
        )
        local_export = await _dispatch(
            runtime.bus,
            "export/prepare",
            f"hero:{pack_name}:local-export",
            {
                "project_id": project_id,
                "purpose": "privacy-safe local Hero receipt",
                "audience": "authorized local reviewer",
            },
        )
        memory = await _dispatch(
            runtime.bus,
            "memory/list",
            f"hero:{pack_name}:memory",
            {"project_id": project_id},
        )
        receipt_audit = await _dispatch(
            runtime.bus,
            "receipt/audit/read",
            f"hero:{pack_name}:receipt-audit",
            {"project_id": project_id},
        )
        project = await _dispatch(
            runtime.bus,
            "project/read",
            f"hero:{pack_name}:project-read",
            {"project_id": project_id},
        )
        result = _result(
            pack_name=pack_name,
            execution_mode=execution_mode,
            pack=pack,
            started=started,
            analysis=analysis,
            evidence=evidence,
            branch=branch,
            restore_preview=restore_preview,
            baseline_preview=baseline_preview,
            closure=closure,
            purge_preview=purge_preview,
            local_export=local_export,
            memory=memory,
            receipt_audit=receipt_audit,
            project=project,
            policy_payload=effective_policy,
            operation_trace=SqliteOperationStore(runtime.ledger.engine).list_by_project(
                project_id
            ),
            tui_operation_trace=SqliteOperationStore(runtime.ledger.engine).list_by_project(
                project_id,
                idempotency_prefix=f"tui:tui:hero:{pack_name}:",
            ),
        )
        if manifest_path is not None:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_bytes(
                orjson.dumps(
                    result.manifest,
                    option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
                )
            )
        return result
    finally:
        runtime.close()


def _result(
    *,
    pack_name: str,
    execution_mode: str,
    pack: object,
    started: dict[str, JsonValue],
    analysis: dict[str, JsonValue],
    evidence: dict[str, JsonValue],
    branch: dict[str, JsonValue],
    restore_preview: dict[str, JsonValue],
    baseline_preview: dict[str, JsonValue],
    closure: dict[str, JsonValue],
    purge_preview: dict[str, JsonValue],
    local_export: dict[str, JsonValue],
    memory: dict[str, JsonValue],
    receipt_audit: dict[str, JsonValue],
    project: dict[str, JsonValue],
    policy_payload: dict[str, object],
    operation_trace: tuple[OperationRecord, ...],
    tui_operation_trace: tuple[OperationRecord, ...],
) -> HeroRunResult:
    from thoth.domain.projectpack import LoadedProjectPack

    loaded = cast(LoadedProjectPack, pack)
    spans = cast(list[dict[str, JsonValue]], evidence["spans"])
    selected_refs = set(cast(list[str], analysis["selected_evidence_refs"]))
    after_cutoff = [item for item in spans if item["cutoff_state"] == "AFTER_CUTOFF"]
    portfolio = cast(dict[str, JsonValue], analysis["portfolio"])
    hypotheses = cast(list[dict[str, JsonValue]], portfolio["hypotheses"])
    action_plan = cast(dict[str, JsonValue], analysis["action_plan"])
    actions = cast(list[dict[str, JsonValue]], action_plan["alternatives"])
    acquisition = analysis.get("autonomous_acquisition")
    counter = analysis.get("critical_counter_search")
    r2 = analysis.get("r2_closed_loop")
    commit = cast(dict[str, JsonValue], analysis["commit"])
    commit_receipt = cast(dict[str, JsonValue], commit["receipt"])
    full_memory = cast(dict[str, JsonValue], analysis["full_project_memory"])
    compiled_spec = (
        cast(dict[str, JsonValue], r2.get("compiled_spec", {})) if isinstance(r2, dict) else {}
    )
    sandbox_result = (
        cast(dict[str, JsonValue], r2.get("sandbox_result", {})) if isinstance(r2, dict) else {}
    )
    r2_outcome = cast(dict[str, JsonValue], r2.get("outcome", {})) if isinstance(r2, dict) else {}
    r2_observations = (
        cast(list[JsonValue], r2.get("observation_refs", [])) if isinstance(r2, dict) else []
    )
    receipts = cast(list[dict[str, JsonValue]], receipt_audit["receipts"])
    closure_value = cast(dict[str, JsonValue], closure["closure"])
    baseline_candidate = cast(dict[str, JsonValue], baseline_preview["baseline_candidate"])
    export_value = cast(dict[str, JsonValue], local_export["export"])
    project_value = project
    stages: dict[str, object] = {
        "source_cutoff": {
            "label": execution_mode,
            "eligible": sum(item["cutoff_state"] == "ELIGIBLE" for item in spans),
            "after_cutoff_excluded": sum(
                str(item["span_id"]) not in selected_refs for item in after_cutoff
            ),
        },
        "reasoning": {
            "label": execution_mode,
            "hypotheses": len(hypotheses),
            "actions": len(actions),
            "assessment_status": cast(dict[str, JsonValue], analysis["assessment"])[
                "derived_status"
            ],
        },
        "acquisition": {
            "label": execution_mode,
            "terminal_state": (
                "NOT_TRIGGERED"
                if not isinstance(acquisition, dict)
                else acquisition.get("terminal_state")
            ),
        },
        "counter_search": {
            "label": execution_mode,
            "terminal_state": (
                "NOT_TRIGGERED" if not isinstance(counter, dict) else counter.get("terminal_state")
            ),
        },
        "sandbox_outcome": {
            "label": execution_mode,
            "terminal_state": (
                "NOT_TRIGGERED" if not isinstance(r2, dict) else r2.get("terminal_state")
            ),
            "scientific_truth_state": (
                "NOT_CERTIFIED"
                if not isinstance(r2, dict)
                else r2.get("scientific_truth_state", "NOT_CERTIFIED")
            ),
            "runtime_profile": compiled_spec.get("runtime_profile"),
            "network_policy": compiled_spec.get("network_policy"),
            "policy_digest": compiled_spec.get("policy_digest"),
            "current_head_set_digest": compiled_spec.get("current_head_set_digest"),
            "input_context_digest": compiled_spec.get("input_context_digest"),
            "action_id": compiled_spec.get("action_id"),
            "exit_code": sandbox_result.get("exit_code"),
            "cleanup_state": sandbox_result.get("cleanup_state"),
            "runtime_version": sandbox_result.get("runtime_version"),
            "security_tier": sandbox_result.get("security_tier"),
            "observation_count": len(r2_observations),
            "outcome_present": bool(r2_outcome.get("outcome_id")),
            "hypothesis_action_revision_present": (
                isinstance(r2, dict)
                and r2.get("terminal_state") == "COMPLETED"
                and bool(r2_outcome.get("outcome_id"))
            ),
        },
        "revision_memory_receipt": {
            "label": execution_mode,
            "committed_revision_count": len(
                cast(list[str], commit.get("committed_revision_ids", []))
            ),
            "committed_memory_count": len(cast(list[object], full_memory.get("committed", []))),
            "reviewed_memory_count": sum(
                len(cast(list[object], full_memory.get(key, [])))
                for key in ("committed", "revised", "held", "quarantined")
            ),
            "legacy_memory_projection_count": len(cast(list[object], memory.get("memories", []))),
            "receipt_count": len(receipts),
            "dag_present": isinstance(receipt_audit.get("dag"), dict),
            "semantic_truth_certified": bool(commit_receipt.get("semantic_truth_certified", False)),
        },
        "closure_baseline_r3_preview": {
            "label": "PREVIEW",
            "closure_status": closure_value["status"],
            "baseline_candidate_digest": baseline_candidate["record_digest"],
            "baseline_decision_performed": False,
            "branch_content_mutated": branch["content_mutated"],
            "restore_preview_mutated": False,
            "restore_preview_available": bool(restore_preview),
            "r3_action_performed": False,
            "deletion_performed": purge_preview["deletion_performed"],
            "local_export_state": export_value["release_state"],
        },
    }
    normal_entries = [
        operation for operation in tui_operation_trace if operation.method == "thread/input"
    ]
    if len(normal_entries) != 1:
        raise RuntimeError(
            f"Hero requires exactly one observed thread/input, got {len(normal_entries)}"
        )
    manual_semantic = [
        operation
        for operation in operation_trace
        if operation.method in _MANUAL_SEMANTIC_METHODS
    ]
    trace_payload = [
        {
            "operation_id": operation.operation_id,
            "method": operation.method,
            "idempotency_key": operation.idempotency_key,
            "scope_digest": operation.scope_digest,
            "state": operation.state.value,
        }
        for operation in operation_trace
    ]
    normal_entry_trace = {
        "source": "COMMAND_BUS_OPERATION_STORE",
        "thread_input_count": len(normal_entries),
        "manual_semantic_rpc_count": len(manual_semantic),
        "operation_ids": [operation.operation_id for operation in tui_operation_trace],
        "methods": [operation.method for operation in tui_operation_trace],
        "trace_digest": domain_digest(
            "HERO_OPERATION_TRACE",
            "1.0.0",
            canonical_payload({"operations": trace_payload}),
        ),
    }
    manifest: dict[str, object] = {
        "schema_version": "1.0.0",
        "pack_name": pack_name,
        "pack_id": loaded.project.pack_id,
        "project_id": loaded.project.project_id,
        "thread_id": str(started["thread_id"]),
        "overlay": project_value["overlay"],
        "execution_mode": execution_mode,
        "normal_entry_method": normal_entries[0].method,
        "normal_entry_surface": "NATURAL_LANGUAGE_TUI",
        "manual_semantic_rpc_assembly": bool(manual_semantic),
        "normal_entry_trace": normal_entry_trace,
        "trace_contracts": _contract_digests(policy_payload),
        "runtime_source_paths": [source.path for source in loaded.sources],
        "source_digests": [source.byte_sha256 for source in loaded.sources],
        "model_ids": cast(list[str], analysis["model_ids"]),
        "stages": stages,
        "external_write_performed": False,
        "r3_or_r4_executed": False,
        "beneficiary_pdf_in_public_bundle": False,
        "oracle_runtime_visible": False,
        "privacy_safe": True,
    }
    hypothesis_statements = [str(item["statement"]) for item in hypotheses]
    observations: dict[str, object] = {
        "hypothesis_statements": hypothesis_statements,
        "hypothesis_loci": [str(item["primary_locus"]) for item in hypotheses],
        "derived_status": cast(dict[str, JsonValue], analysis["assessment"])["derived_status"],
        "action_states": [
            {"risk_tier": item["risk_tier"], "state": item["state"]} for item in actions
        ],
        "semantic_truth_receipts": [
            bool(item.get("semantic_truth_certified", False)) for item in receipts
        ],
        "runtime_source_paths": manifest["runtime_source_paths"],
        "source_texts": [str(item["exact_text"]) for item in spans],
        "external_write_performed": False,
        "r3_or_r4_executed": False,
        "oracle_runtime_visible": False,
    }
    return HeroRunResult(manifest=manifest, observations=observations)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--execution-mode",
        choices=("LIVE", "SEALED_REPLAY", "PREVIEW"),
        default="LIVE",
    )
    parser.add_argument("--manifest", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = asyncio.run(
        run_current_hero(
            pack_name=args.pack,
            workspace=args.workspace,
            execution_mode=args.execution_mode,
            manifest_path=args.manifest,
        )
    )
    print(orjson.dumps(result.manifest, option=orjson.OPT_SORT_KEYS).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

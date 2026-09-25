from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from scripts.check_ocp_extensions import extension_manifest_errors
from scripts.checkpoint_commit_contract import prepare_checkpoint, validate_checkpoint_action
from scripts.required_architecture_checks import CHECKS, validate_check_results
from scripts.verification_identity import assert_unchanged, index_digest, repository_digest
from tests.architecture.test_checkpoint_commit_contract import git, repository, verified_gate
from tests.architecture.test_reevaluated_guards import mirror


@pytest.mark.parametrize(
    "mutation", ["qualified_clock", "same_name", "protocol", "signature", "registration"]
)
def test_factory_binding_mutations_fail_closed(tmp_path: Path, mutation: str) -> None:
    manifest = mirror(tmp_path)
    model = next(item for item in manifest["extension_points"] if item["name"] == "MODEL")
    if mutation == "qualified_clock":
        model["factory_target"] = "thoth.ports.runtime.ClockPort"
    elif mutation == "same_name":
        (tmp_path / "src/thoth/adapters/imposter.py").write_text(
            "class RegisteredModelResolver:\n    def now(self): return None\n"
        )
        model["factory_target"] = "thoth.adapters.imposter.RegisteredModelResolver"
    elif mutation == "protocol":
        model["factory_port"] = "thoth.ports.runtime.ClockPort"
    elif mutation == "signature":
        path = tmp_path / "src/thoth/adapters/models/registry.py"
        path.write_text(path.read_text().replace("-> ModelPort:", "-> str:"))
    else:
        module = model["composition_target"].rsplit(".", 1)[0]
        path = tmp_path / "src" / (module.replace(".", "/") + ".py")
        tree = ast.parse(path.read_text(encoding="utf-8"))

        class RemoveRegistration(ast.NodeTransformer):
            removed = 0

            def visit_Expr(self, node: ast.Expr) -> ast.AST | None:
                call = node.value
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "defaults"
                    and call.func.attr == "register"
                ):
                    self.removed += 1
                    return None
                return node

        mutation_pass = RemoveRegistration()
        changed = mutation_pass.visit(tree)
        assert mutation_pass.removed > 0, (
            "registration mutation did not change the declared factory"
        )
        path.write_text(ast.unparse(changed), encoding="utf-8")
    (tmp_path / "config/architecture-conformance.json").write_text(json.dumps(manifest))
    assert extension_manifest_errors(tmp_path)


@pytest.mark.parametrize("mutation", ["missing", "skip", "failed", "duplicate"])
def test_required_gate_checks_cannot_be_skipped(mutation: str) -> None:
    checks: list[dict[str, object]] = [
        {"check": name, "verdict": "PASS", "errors": [], "exit_code": 0} for name in CHECKS
    ]
    validate_check_results(checks)
    if mutation == "missing":
        checks.pop()
    elif mutation == "skip":
        checks[0]["skipped"] = True
    elif mutation == "failed":
        checks[0]["exit_code"] = 1
    else:
        checks[-1] = checks[0]
    with pytest.raises(ValueError, match="check"):
        validate_check_results(checks)


def test_verification_detects_source_and_index_drift(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repository(repo)
    source, index = repository_digest(repo), index_digest(repo)
    assert_unchanged(repo, source, index)
    (repo / "README.md").write_text("new\n")
    with pytest.raises(ValueError, match="source/index"):
        assert_unchanged(repo, source, index)
    source = repository_digest(repo)
    git(repo, "add", "README.md")
    with pytest.raises(ValueError, match="source/index"):
        assert_unchanged(repo, source, index)


def test_real_git_mixed_state_and_branch_drift_are_rejected(tmp_path: Path) -> None:
    repo, gate = tmp_path / "repo", tmp_path / "gate"
    repository(repo)
    (repo / "README.md").write_text("staged\n")
    git(repo, "add", "README.md")
    (repo / "README.md").write_text("unstaged\n")
    verified_gate(gate, repo)
    with pytest.raises(ValueError, match="fully staged"):
        prepare_checkpoint(root=repo, gate_dir=gate, remote_name="origin", branch="main")
    git(repo, "add", "README.md")
    prepare_checkpoint(root=repo, gate_dir=gate, remote_name="origin", branch="main")
    git(repo, "checkout", "-b", "other")
    with pytest.raises(ValueError, match="branch changed"):
        validate_checkpoint_action(root=repo, gate_dir=gate, action="commit")


def test_checkpoint_cannot_reuse_an_older_active_verification(tmp_path: Path) -> None:
    repo, gate = tmp_path / "repo", tmp_path / "gate"
    repository(repo)
    (repo / "README.md").write_text("candidate\n")
    git(repo, "add", "README.md")
    verified_gate(gate, repo)
    prepare_checkpoint(root=repo, gate_dir=gate, remote_name="origin", branch="main")
    verified_gate(gate, repo)
    with pytest.raises(ValueError, match="active verification"):
        validate_checkpoint_action(root=repo, gate_dir=gate, action="commit")


def test_checkpoint_binds_effective_push_url_not_only_fetch_url(tmp_path: Path) -> None:
    repo, gate = tmp_path / "repo", tmp_path / "gate"
    repository(repo)
    (repo / "README.md").write_text("candidate\n")
    git(repo, "add", "README.md")
    verified_gate(gate, repo)
    prepare_checkpoint(root=repo, gate_dir=gate, remote_name="origin", branch="main")
    git(repo, "remote", "set-url", "--push", "origin", "https://example.invalid/other.git")
    with pytest.raises(ValueError, match="remote"):
        validate_checkpoint_action(root=repo, gate_dir=gate, action="commit")

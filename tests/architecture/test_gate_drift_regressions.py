from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from scripts.check_ocp_extensions import extension_manifest_errors
from scripts.required_architecture_checks import CHECKS, validate_check_results
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

"""Exact patch preview in a disposable tree; never modify the active repository."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from scripts.required_architecture_checks import run_checks


def apply_candidate_patch(original: bytes, body: str) -> bytes:
    """Deliberately reject fuzzy/ambiguous patches instead of predicting tool fuzz."""
    text = original.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    chunks: list[list[str]] = []
    for line in body.splitlines():
        if line.startswith("@@"):
            chunks.append([])
        elif line == "*** End of File":
            continue
        elif line[:1] in {" ", "+", "-"}:
            if not chunks:
                chunks.append([])
            chunks[-1].append(line)
        else:
            raise ValueError("unsupported rule patch syntax")
    if not chunks:
        raise ValueError("empty rule patch")
    offset = 0
    for chunk in chunks:
        before = [line[1:] for line in chunk if not line.startswith("+")]
        after = [line[1:] for line in chunk if not line.startswith("-")]
        if not before:
            raise ValueError("rule update requires exact existing context")
        matches = [
            index for index in range(offset, len(lines) - len(before) + 1)
            if lines[index:index + len(before)] == before
        ]
        if len(matches) != 1:
            raise ValueError("rule patch context is missing or ambiguous")
        index = matches[0]
        lines[index:index + len(before)] = after
        offset = index + len(after)
    return (newline.join(lines) + (newline if text.endswith("\n") else "")).encode("utf-8")


def patch_candidates(root: Path, patch: str) -> dict[str, bytes]:
    lines = patch.splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("rules require a complete apply_patch envelope")
    result: dict[str, bytes] = {}
    index = 1
    while index < len(lines) - 1:
        header = lines[index]
        if not header.startswith(("*** Update File: ", "*** Add File: ")):
            raise ValueError("rule candidate supports update/add only; no move/delete")
        raw = header.split(": ", 1)[1]
        if raw.replace("\\", "/").startswith(root.name + "/"):
            raw = raw.replace("\\", "/")[len(root.name) + 1:]
        path = (root / raw).resolve()
        relative = path.relative_to(root.resolve()).as_posix()
        if path.is_symlink() or relative in result:
            raise ValueError("duplicate or linked rule candidate")
        index += 1
        start = index
        while index < len(lines) - 1 and not lines[index].startswith("*** "):
            index += 1
        if index < len(lines) - 1 and lines[index] == "*** End of File":
            index += 1
        body = lines[start:index]
        if header.startswith("*** Add File: "):
            if path.exists() or not body or any(not line.startswith("+") for line in body):
                raise ValueError("invalid added rule candidate")
            result[relative] = ("\n".join(line[1:] for line in body) + "\n").encode()
        else:
            result[relative] = apply_candidate_patch(path.read_bytes(), "\n".join(body))
    return result


def _preserve_ratchets(root: Path, candidates: dict[str, bytes]) -> None:
    budget = "config/module-responsibility-budget.json"
    if budget in candidates:
        old = json.loads((root / budget).read_bytes())
        new = json.loads(candidates[budget])
        for key in ("module_line_limit", "function_line_limit"):
            if new.get(key) != old.get(key):
                raise ValueError("rule update cannot change size limits")
        if new["scan_roots"] != old["scan_roots"]:
            raise ValueError("rule update cannot change scan roots")
        for name, baseline in new["watched_long_functions"].items():
            if (
                name not in old["watched_long_functions"]
                or baseline > old["watched_long_functions"][name]
            ):
                raise ValueError("rule update cannot enlarge a ratchet")
        for name, entry in new["watched_modules"].items():
            prior = old["watched_modules"].get(name)
            if prior is None or entry["baseline_lines"] > prior["baseline_lines"]:
                raise ValueError("rule update cannot enlarge a module ratchet")
            if not set(entry["top_level_symbols"]) <= set(prior["top_level_symbols"]):
                raise ValueError("rule update cannot add hotspot responsibilities")
    manifest = "config/architecture-conformance.json"
    if manifest in candidates:
        old = json.loads((root / manifest).read_bytes())
        new = json.loads(candidates[manifest])
        old_ids = {item["id"] for item in old["known_exceptions"]}
        if any(item["id"] not in old_ids for item in new["known_exceptions"]):
            raise ValueError("rule update cannot add architecture exceptions")
        if new["rule_documents"] != old["rule_documents"]:
            raise ValueError("changing the rule inventory requires owner review")


def validate_candidate(root: Path, candidates: dict[str, bytes]) -> list[dict[str, object]]:
    from scripts.architecture_contract import load_manifest, rule_bundle_paths
    from scripts.verification_identity import repository_digest, reviewed_paths

    deadline = time.monotonic() + 45.0
    before = repository_digest(root)
    _preserve_ratchets(root, candidates)
    # Copy only reviewable source, not .git, credentials, runtimes or the active gate.
    with tempfile.TemporaryDirectory(prefix="thoth-rule-candidate-") as temporary:
        snapshot = Path(temporary)
        for relative in reviewed_paths(root):
            source = root / relative
            if not source.is_file():
                continue
            if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
                raise ValueError("candidate source contains a linked path")
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        for relative, data in candidates.items():
            target = (snapshot / relative).resolve()
            if not target.is_relative_to(snapshot) or target == snapshot:
                raise ValueError("candidate path escapes snapshot")
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative.endswith(".py"):
                ast.parse(data, filename=relative)
            target.write_bytes(data)
        # Architecture PASS comes only from the currently trusted validator inventory.
        # Candidate guard code is a separate code-review/test subject, never its own oracle.
        candidate_guard_bytes = {}
        for relative in rule_bundle_paths(load_manifest(root), root):
            if relative.startswith("scripts/") or relative.startswith(".codex/hooks/"):
                target = snapshot / relative
                if relative in candidates:
                    candidate_guard_bytes[relative] = target.read_bytes()
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / relative, target)
        results = run_checks(snapshot, deadline=deadline)
        # Import smoke may exercise candidate guard code, but cannot issue architecture PASS.
        for relative, data in candidate_guard_bytes.items():
            (snapshot / relative).write_bytes(data)
        for hook in ("pre_tool_policy.py", "stop_acceptance_gate.py", "session_start.py"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("rule candidate hook-smoke budget exhausted")
            result = subprocess.run(
                [sys.executable, str(snapshot / ".codex/hooks" / hook)], cwd=snapshot,
                input=json.dumps({
                    "tool_name": "Bash",
                    "tool_input": {"command": "Get-Content -LiteralPath AGENTS.md"},
                }),
                encoding="utf-8", capture_output=True, check=False, timeout=remaining,
            )
            if result.returncode:
                raise ValueError(f"candidate hook import/smoke failed: {hook}: {result.stderr}")
            if result.stdout.strip():
                output = json.loads(result.stdout)
                if output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
                    raise ValueError("candidate hook rejected the intrinsic read probe")
    if repository_digest(root) != before:
        raise ValueError("repository changed during rule candidate verification")
    return results

"""Codex Stop wire semantics with bounded continuation, not a scheduler."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

MAX_CONTINUATIONS = 32


def progress_fingerprint(preflight: dict[str, Any], source: str) -> str:
    """Reissuing a timestamped preflight is not implementation progress."""
    value = {
        "acceptance": preflight.get("acceptance_id"),
        "plan": preflight.get("plan_id"),
        "node": preflight.get("node_id"),
        "source": source,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def stop_response(
    gate: Path, event: dict[str, Any], state: str, reason: str, progress: str
) -> dict[str, Any]:
    if state == "DONE":
        return {}
    if state == "CAN_CONTINUE":
        session = str(event.get("session_id", "unknown")) + ":" + str(event.get("plan_id", ""))
        key = hashlib.sha256(session.encode()).hexdigest()
        path = gate / "stop-progress" / f"{key}.json"
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        count = previous.get("continuations", 0)
        if not isinstance(count, int) or count < 0:
            raise ValueError("invalid Stop continuation budget")
        if count >= MAX_CONTINUATIONS:
            state = "CONTINUATION_BUDGET_EXHAUSTED_OWNER_REQUIRED"
        elif previous.get("progress") != progress:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"progress": progress, "continuations": count + 1}), encoding="utf-8"
            )
            os.replace(temporary, path)
            return {"decision": "block", "reason": reason}
        else:
            state = "NO_PROGRESS_OWNER_REQUIRED"
    return {
        "continue": False,
        "stopReason": reason,
        "systemMessage": (
            f"THOTH {state}: {reason} Do not claim completion or repeat unchanged work."
        ),
    }


def next_plan_node(root: Path, preflight: dict[str, Any]) -> str | None:
    plan_id = preflight.get("plan_id")
    if plan_id is None:
        return None
    catalogue = json.loads((root / "config/workflow-plans.json").read_bytes())
    if catalogue.get("schema_version") != "1.0.0":
        raise ValueError("invalid workflow-plan catalogue")
    plan = catalogue["plans"].get(plan_id)
    if plan is None:
        raise ValueError("unregistered plan; node receipt does not establish whole-plan completion")
    nodes = plan["ordered_nodes"]
    if (
        not isinstance(nodes, list) or not nodes
        or any(not isinstance(node, str) or not re.fullmatch(r"[A-Z][A-Z0-9_-]{0,63}", node)
               for node in nodes)
        or len(nodes) != len(set(nodes))
    ):
        raise ValueError("workflow plan must have unique ordered nodes")
    current = preflight.get("node_id")
    if current not in nodes:
        raise ValueError("verified node is not in the registered plan")
    index = nodes.index(current)
    return nodes[index + 1] if index + 1 < len(nodes) else None

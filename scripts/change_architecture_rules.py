"""Operator-facing, exact-proposal metadata transaction CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.architecture_contract import ROOT
from scripts.rule_transaction_contract import proposal_preview
from scripts.rule_transaction_engine import MetadataRuleTransaction


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    stage = sub.add_parser("stage")
    stage.add_argument("--candidate", required=True)
    for mode in ("apply", "resume", "rollback"):
        command = sub.add_parser(mode)
        command.add_argument("--proposal", required=True)
        command.add_argument("--approve-proposal", required=True)
    args = parser.parse_args()
    gate = Path(os.environ.get("THOTH_GATE_DIR", ROOT / ".thoth/architecture"))
    engine = MetadataRuleTransaction(ROOT, gate)
    if args.mode == "stage":
        path = (ROOT / args.candidate).absolute()
        allowed = ROOT / ".thoth/rule-candidates"
        if (
            path.resolve() != path or not path.is_relative_to(allowed)
            or path.stat().st_size > 4_000_000
        ):
            raise ValueError("candidate must be a bounded regular file in .thoth/rule-candidates")
        value = json.loads(path.read_bytes())
        if set(value) != {"files"} or not isinstance(value["files"], dict):
            raise ValueError("candidate schema requires only a files map")
        if any(not isinstance(text, str) for text in value["files"].values()):
            raise ValueError("candidate values must be full UTF-8 metadata text")
        proposal = engine.stage({name: text.encode() for name, text in value["files"].items()})
        result = proposal_preview(proposal)
    else:
        action = getattr(engine, args.mode)
        result = action(args.proposal, args.approve_proposal)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

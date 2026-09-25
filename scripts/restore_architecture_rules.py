from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.architecture_contract import ROOT
from scripts.rule_recovery_contract import recovery_preview, restore_rules


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", required=True)
    parser.add_argument("--expected-drift")
    args = parser.parse_args()
    gate = Path(os.environ.get("THOTH_GATE_DIR", ROOT / ".thoth/architecture"))
    preflight = json.loads((gate / "preflight.json").read_bytes())
    if preflight.get("preflight_receipt_id") != args.preflight:
        raise ValueError("requested preflight is not active")
    result = (
        restore_rules(ROOT, gate, preflight, args.expected_drift)
        if args.expected_drift else recovery_preview(ROOT, gate, preflight)
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

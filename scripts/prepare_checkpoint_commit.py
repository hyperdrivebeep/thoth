from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.architecture_contract import ROOT
from scripts.checkpoint_commit_contract import prepare_checkpoint


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote", required=True)
    parser.add_argument("--branch", required=True)
    args = parser.parse_args()
    payload = prepare_checkpoint(
        root=ROOT,
        gate_dir=ROOT / ".thoth" / "architecture",
        remote_name=args.remote,
        branch=args.branch,
    )
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

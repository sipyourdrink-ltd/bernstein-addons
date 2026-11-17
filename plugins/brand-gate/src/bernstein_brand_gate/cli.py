"""Explicit pre-action command: 0 permits, 2 blocks."""

import argparse
import json
import os
import sys
from pathlib import Path

from bernstein_brand_gate.config import load_config
from bernstein_brand_gate.gate import check


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="one domain or HTTP(S) URL")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--session", required=True)
    parser.add_argument("--tool", required=True)
    args = parser.parse_args(argv)
    try:
        env_path = os.environ.get("BERNSTEIN_BRAND_GATE_CONFIG")
        config = load_config(args.config or (Path(env_path) if env_path else None))
        if config is None:
            print(json.dumps({"decision": "disabled", "checked": False}))
            return 0
        if args.tool not in config.tools:
            print("brand-gate: tool is not configured; action blocked", file=sys.stderr)
            return 2
        receipt = check(args.target, config, session_id=args.session, tool=args.tool)
        print(json.dumps(receipt, sort_keys=True))
        return 0 if receipt["decision"] == "allow" else 2
    except Exception:
        print("brand-gate: configuration, check or journal unavailable; action blocked", file=sys.stderr)
        return 2

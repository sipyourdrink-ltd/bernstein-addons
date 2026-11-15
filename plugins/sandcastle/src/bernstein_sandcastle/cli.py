"""Explicit entry point for one Bernstein task attempt."""

import argparse
import json
import sys
from pathlib import Path

from .adapter import Hold, run_attempt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one explicitly selected task attempt and write its evidence.",
        epilog=(
            "Install Node dependencies with npm ci --ignore-scripts in the installed "
            "package's runtime directory. Supply a trusted local .mjs file with a "
            "default export containing agent, sandbox and optionally output. "
            "The runtime directory is next to bernstein_sandcastle/adapter.py."
        ),
    )
    parser.add_argument("--repo-dir", required=True, type=Path)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--target-ref", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--timeout", required=True, type=int)
    options = vars(parser.parse_args())
    prompt_file = options.pop("prompt_file")
    try:
        receipt = run_attempt(**options, prompt=prompt_file.read_text(encoding="utf-8"))
    except (Hold, OSError, ValueError) as exc:
        print(f"Attempt unavailable ({type(exc).__name__})", file=sys.stderr)
        return 2
    print(json.dumps(receipt, allow_nan=False))
    return 0 if receipt["status"] == "verified" else 2

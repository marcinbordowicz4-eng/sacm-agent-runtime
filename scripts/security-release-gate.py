#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sacm.security_release_gate import (
    evaluate_gate,
    sign_report,
    verify_signed_report,
)


def _write(path: str, value: dict) -> None:
    Path(path).write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def evaluate(args: argparse.Namespace) -> int:
    result = evaluate_gate(
        policy_path=Path(args.policy),
        artifacts=Path(args.artifacts),
        junit_path=Path(args.junit),
        git_sha=args.git_sha,
    )
    _write(args.output, result.report)
    print(json.dumps({"status": result.status, "reasons": result.reasons}, indent=2))
    return {"PASS": 0, "FAIL": 1, "INCOMPLETE": 2}[result.status]


def sign(args: argparse.Namespace) -> int:
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    _write(args.output, sign_report(report, args.key, args.key_id))
    return 0


def verify(args: argparse.Namespace) -> int:
    signed = json.loads(Path(args.signed_report).read_text(encoding="utf-8"))
    valid, errors = verify_signed_report(signed)
    report = signed.get("statement", {}).get("predicate", {})
    gate_status = report.get("status") if isinstance(report, dict) else None
    print(
        json.dumps(
            {
                "signature_status": "VALID" if valid else "INVALID",
                "gate_status": gate_status,
                "errors": errors,
            }
        )
    )
    if not valid:
        return 1
    return {"PASS": 0, "FAIL": 1, "INCOMPLETE": 2}.get(gate_status, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate release security evidence.")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("evaluate")
    check.add_argument("--policy", required=True)
    check.add_argument("--artifacts", required=True)
    check.add_argument("--junit", required=True)
    check.add_argument("--git-sha", required=True)
    check.add_argument("--output", required=True)
    check.set_defaults(handler=evaluate)
    create = commands.add_parser("sign-report")
    create.add_argument("--report", required=True)
    create.add_argument("--key", required=True)
    create.add_argument("--key-id", default="github-actions-ephemeral")
    create.add_argument("--output", required=True)
    create.set_defaults(handler=sign)
    inspect = commands.add_parser("verify-report")
    inspect.add_argument("--signed-report", required=True)
    inspect.set_defaults(handler=verify)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())

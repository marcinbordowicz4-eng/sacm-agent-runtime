#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path

from sacm.core.supply_chain_service import SupplyChainService


def generate(args: argparse.Namespace) -> int:
    subject = Path(args.subject)
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {
                "name": args.name or subject.name,
                "digest": {"sha256": hashlib.sha256(subject.read_bytes()).hexdigest()},
            }
        ],
        "predicateType": args.predicate_type,
        "predicate": json.loads(Path(args.predicate).read_text(encoding="utf-8")),
    }
    signed = SupplyChainService.sign_statement(
        statement,
        private_key_file=args.key,
        key_id=args.key_id,
    )
    Path(args.output).write_text(
        json.dumps(signed, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


def verify(args: argparse.Namespace) -> int:
    signed = json.loads(Path(args.attestation).read_text(encoding="utf-8"))
    hmac_key = (
        Path(args.hmac_key_file).read_text(encoding="utf-8").strip()
        if args.hmac_key_file
        else None
    )
    result = SupplyChainService.verify_signed_statement(
        signed, hmac_key=hmac_key
    )
    print(result.model_dump_json(indent=2))
    return 0 if result.status == "VALID" else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify canonical local in-toto attestations."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("generate")
    create.add_argument("--subject", required=True)
    create.add_argument("--name")
    create.add_argument("--predicate", required=True)
    create.add_argument("--predicate-type", required=True)
    create.add_argument("--key", required=True, help="Ed25519 private-key file")
    create.add_argument("--key-id")
    create.add_argument("--output", required=True)
    create.set_defaults(handler=generate)

    check = commands.add_parser("verify")
    check.add_argument("--attestation", required=True)
    check.add_argument("--hmac-key-file")
    check.set_defaults(handler=verify)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())

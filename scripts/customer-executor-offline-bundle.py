#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sacm.core.execution_signing import (
    canonical_json,
    public_key_fingerprint,
    verify_ed25519,
)


def generate(output: Path, private_key_path: Path, artifacts: list[Path]) -> str:
    key = serialization.load_pem_private_key(
        private_key_path.read_bytes(), password=None
    )
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Offline bundle signing key must be Ed25519.")
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    files: list[dict[str, Any]] = []
    names: set[str] = set()
    for artifact in artifacts:
        name = artifact.name
        if name in names:
            raise ValueError(f"Duplicate bundle artifact name: {name}")
        names.add(name)
        files.append(
            {
                "name": name,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "size": artifact.stat().st_size,
            }
        )
    manifest = {
        "schema_version": "executor-offline-bundle/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": sorted(files, key=lambda item: str(item["name"])),
        "automatic_update": False,
    }
    envelope = {
        "manifest": manifest,
        "signature": base64.b64encode(key.sign(canonical_json(manifest))).decode(),
        "signature_metadata": {
            "algorithm": "Ed25519",
            "key_fingerprint": public_key_fingerprint(public_pem),
            "public_key": public_pem,
        },
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for artifact in artifacts:
            bundle.write(artifact, f"artifacts/{artifact.name}")
        bundle.writestr(
            "bundle-manifest.json",
            json.dumps(envelope, sort_keys=True, indent=2),
        )
    return public_key_fingerprint(public_pem)


def verify(bundle_path: Path, trusted_key_fingerprint: str) -> None:
    with zipfile.ZipFile(bundle_path) as bundle:
        names = set(bundle.namelist())
        if "bundle-manifest.json" not in names:
            raise ValueError("Offline bundle manifest is missing.")
        envelope = json.loads(bundle.read("bundle-manifest.json"))
        manifest = envelope["manifest"]
        metadata = envelope["signature_metadata"]
        if metadata.get("algorithm") != "Ed25519":
            raise ValueError("Unsupported offline bundle signature.")
        if public_key_fingerprint(metadata["public_key"]) != metadata["key_fingerprint"]:
            raise ValueError("Offline bundle signing fingerprint mismatch.")
        if metadata["key_fingerprint"].lower() != trusted_key_fingerprint.lower():
            raise ValueError("Offline bundle is not signed by the trusted release key.")
        verify_ed25519(metadata["public_key"], manifest, envelope["signature"])
        expected_names = {"bundle-manifest.json"}
        for item in manifest["files"]:
            name = f"artifacts/{item['name']}"
            expected_names.add(name)
            value = bundle.read(name)
            if len(value) != item["size"]:
                raise ValueError(f"Offline bundle size mismatch: {item['name']}")
            if hashlib.sha256(value).hexdigest() != item["sha256"]:
                raise ValueError(f"Offline bundle checksum mismatch: {item['name']}")
        if names != expected_names:
            raise ValueError("Offline bundle contains undeclared files.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate or verify a signed customer-executor offline bundle."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("generate")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--private-key", type=Path, required=True)
    create.add_argument("artifacts", type=Path, nargs="+")
    check = subparsers.add_parser("verify")
    check.add_argument("bundle", type=Path)
    check.add_argument("--trusted-key-fingerprint", required=True)
    args = parser.parse_args()
    if args.command == "generate":
        fingerprint = generate(args.output, args.private_key, args.artifacts)
        print(f"Offline bundle created. Signing key fingerprint: {fingerprint}")
    else:
        verify(args.bundle, args.trusted_key_fingerprint)
        print("Offline bundle verified.")


if __name__ == "__main__":
    main()

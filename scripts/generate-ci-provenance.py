#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--sbom", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    sbom_hash = hashlib.sha256(Path(args.sbom).read_bytes()).hexdigest()
    image_digest = args.image_digest.removeprefix("sha256:")
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {"name": args.image, "digest": {"sha256": image_digest}},
        ],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://sacm.dev/github-actions-container/v1",
                "externalParameters": {
                    "source": {
                        "repository": args.repository,
                        "revision": args.revision,
                    },
                    "commands": ["docker build", "syft sbom", "trivy scans"],
                },
                "resolvedDependencies": [
                    {
                        "uri": Path(args.sbom).name,
                        "digest": {"sha256": sbom_hash},
                    }
                ],
            },
            "runDetails": {
                "builder": {"id": "https://github.com/actions/runner"},
                "metadata": {
                    "executor": "github-hosted:ubuntu-latest",
                    "environment": {"workflow": "enterprise-supply-chain"},
                },
            },
        },
    }
    Path(args.output).write_text(
        json.dumps(statement, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import argparse
import json

from sacm.core.execution_plane_service import ExecutionPlaneService
from sacm.infrastructure.db.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover expired and orphaned execution jobs.")
    parser.add_argument("--organization-id")
    args = parser.parse_args()
    with SessionLocal() as db:
        report = ExecutionPlaneService(db).recover_orphaned(
            organization_id=args.organization_id
        )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()

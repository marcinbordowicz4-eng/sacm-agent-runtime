#!/usr/bin/env python3
import argparse
import json

from sacm.core.resilience_service import BackupService
from sacm.infrastructure.db.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a backup in an isolated database.")
    parser.add_argument("--backup-id", required=True)
    parser.add_argument("--organization-id")
    parser.add_argument("--actor", default="dr-controller")
    parser.add_argument("--destructive", action="store_true")
    parser.add_argument("--target-database")
    parser.add_argument("--guard-token")
    parser.add_argument("--keep-isolated-database", action="store_true")
    args = parser.parse_args()

    with SessionLocal() as db:
        drill = BackupService(db).verify_restore(
            args.backup_id,
            args.actor,
            destructive_restore=args.destructive,
            target_database=args.target_database,
            guard_token=args.guard_token,
            keep_isolated_database=args.keep_isolated_database,
        )
        print(
            json.dumps(
                {
                    "drill_id": drill.id,
                    "status": drill.status,
                    "measured_rpo_seconds": drill.measured_rpo_seconds,
                    "measured_rto_seconds": drill.measured_rto_seconds,
                }
            )
        )
        if drill.status != "PASSED":
            raise SystemExit(1)


if __name__ == "__main__":
    main()

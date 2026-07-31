#!/usr/bin/env python3
import argparse
import json

from sacm.core.resilience_service import BackupService
from sacm.infrastructure.db.session import SessionLocal
from sacm.schemas.resilience import BackupCreate


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a catalogued PostgreSQL logical backup.")
    parser.add_argument("--database", required=True)
    parser.add_argument("--storage-uri", required=True)
    parser.add_argument("--organization-id")
    parser.add_argument("--actor", default="backup-controller")
    parser.add_argument("--rpo-seconds", type=int, required=True)
    parser.add_argument("--rto-seconds", type=int, required=True)
    args = parser.parse_args()

    with SessionLocal() as db:
        record = BackupService(db).create(
            BackupCreate(
                organization_id=args.organization_id,
                source_database=args.database,
                storage_uri=args.storage_uri,
                rpo_target_seconds=args.rpo_seconds,
                rto_target_seconds=args.rto_seconds,
                execute=True,
                artifact_metadata={"format": "pg_dump-custom"},
                evidence_metadata={"controller": "postgres-logical-backup.py"},
            ),
            args.actor,
        )
        print(json.dumps({"backup_id": record.id, "status": record.status}))
        if record.status != "COMPLETED":
            raise SystemExit(1)


if __name__ == "__main__":
    main()

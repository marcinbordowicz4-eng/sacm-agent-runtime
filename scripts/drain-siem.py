#!/usr/bin/env python3

import argparse
import json

from sacm.core.governance_service import SIEMService
from sacm.infrastructure.db.models import SIEMSink
from sacm.infrastructure.db.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description="Drain pending SACM SIEM audit events.")
    parser.add_argument("--organization-id")
    parser.add_argument("--sink-id")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    db = SessionLocal()
    try:
        query = db.query(SIEMSink).filter(SIEMSink.status == "ACTIVE")
        if args.organization_id:
            query = query.filter(SIEMSink.organization_id == args.organization_id)
        if args.sink_id:
            query = query.filter(SIEMSink.id == args.sink_id)
        reports = [
            SIEMService(db).drain(
                sink.organization_id,
                sink.id,
                sink.created_by,
                limit=args.limit,
            )
            for sink in query.order_by(SIEMSink.created_at)
        ]
        print(json.dumps(reports, sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()

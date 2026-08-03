import os
import time

from rich.console import Console

from sacm.core.workflow_queue_service import WorkflowQueueService
from sacm.infrastructure.db.session import SessionLocal

console = Console()


def main() -> None:
    console.print("[green]SACM worker ready[/green]")
    poll_seconds = float(os.getenv("SACM_WORKER_POLL_SECONDS", "1"))
    run_once = os.getenv("SACM_WORKER_RUN_ONCE", "false").lower() == "true"
    while True:
        db = SessionLocal()
        try:
            result = WorkflowQueueService(db).process_one()
            if result:
                console.print(result)
        finally:
            db.close()
        if run_once:
            return
        if result is None:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    main()

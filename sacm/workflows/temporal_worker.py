"""Temporal worker entrypoint, imported only when the optional dependency is installed."""

import asyncio
import os
from datetime import timedelta

from sacm.infrastructure.db.session import SessionLocal


async def _execute_run(run_id: str) -> dict:
    from sacm.core.local_workflow import LocalWorkflow

    db = SessionLocal()
    try:
        return LocalWorkflow(db).execute(run_id)
    finally:
        db.close()


def main() -> None:
    try:
        from temporalio import activity, workflow
        from temporalio.client import Client
        from temporalio.worker import Worker
    except ImportError as exc:
        raise RuntimeError(
            "Temporal support requires: pip install -e '.[temporal]'"
        ) from exc

    @activity.defn
    async def execute_run(run_id: str) -> dict:
        return await _execute_run(run_id)

    @workflow.defn(name="SACMRunWorkflow")
    class SACMRunWorkflow:
        @workflow.run
        async def run(self, run_id: str) -> dict:
            return await workflow.execute_activity(
                execute_run,
                run_id,
                start_to_close_timeout=timedelta(minutes=30),
            )

    async def serve() -> None:
        client = await Client.connect(
            os.getenv("SACM_TEMPORAL_ADDRESS", "localhost:7233"),
            namespace=os.getenv("SACM_TEMPORAL_NAMESPACE", "default"),
        )
        worker = Worker(
            client,
            task_queue=os.getenv("SACM_TEMPORAL_TASK_QUEUE", "sacm-runs"),
            workflows=[SACMRunWorkflow],
            activities=[execute_run],
        )
        await worker.run()

    asyncio.run(serve())

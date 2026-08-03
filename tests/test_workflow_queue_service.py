from datetime import datetime, timedelta

from sacm.core.run_service import RunService
from sacm.core.lifecycle_metric_service import LifecycleMetricService
from sacm.core.workflow_backend import LocalWorkflowBackend
from sacm.core.workflow_queue_service import WorkflowQueueService
from sacm.schemas.run import RunCreate


def _run(db):
    return RunService(db).create(
        RunCreate(
            title="Queued run",
            description="Execute outside the API request.",
        )
    )


def test_local_backend_returns_durable_submission_without_executing(db):
    run = _run(db)

    result = LocalWorkflowBackend(db).execute(run.id)
    job = WorkflowQueueService(db).get_for_run(run.id)

    assert result["status"] == "SCHEDULED"
    assert result["job_id"] == job.id
    assert job.state == "QUEUED"
    assert RunService(db).get(run.id).status == "CREATED"


def test_workflow_submission_is_idempotent_and_claimed_once(db):
    run = _run(db)
    queue = WorkflowQueueService(db)

    first = queue.submit(run.id)
    repeated = queue.submit(run.id)
    claimed = queue.claim()

    assert repeated.id == first.id
    assert claimed is not None
    assert claimed[0].id == first.id
    assert queue.claim() is None
    metrics = LifecycleMetricService(db).summary(run.id)["metrics"]
    assert {item["metric"] for item in metrics} == {
        "workflow.queue_latency_ms",
        "workflow.queued",
    }


def test_expired_workflow_job_is_requeued(db):
    run = _run(db)
    queue = WorkflowQueueService(db)
    job = queue.submit(run.id)
    claimed = queue.claim()
    assert claimed is not None

    job = queue.get_for_run(run.id)
    job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()

    reclaimed = queue.claim()

    assert reclaimed is not None
    assert reclaimed[0].id == job.id
    assert reclaimed[0].attempt == 2
    metrics = LifecycleMetricService(db).summary(run.id)["metrics"]
    assert any(item["metric"] == "workflow.lease_expired" for item in metrics)


def test_cancelled_workflow_job_cannot_be_claimed(db):
    run = _run(db)
    queue = WorkflowQueueService(db)
    queue.submit(run.id)

    cancelled = queue.cancel(run.id)

    assert cancelled is not None
    assert cancelled.state == "CANCELLED"
    assert queue.claim() is None
    metrics = LifecycleMetricService(db).summary(run.id)["metrics"]
    assert any(item["metric"] == "workflow.queue_cancelled" for item in metrics)

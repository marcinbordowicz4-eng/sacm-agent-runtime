from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SnapshotCreateV1(BaseModel):
    schema_version: Literal["snapshot-create/v1"] = "snapshot-create/v1"
    reason: str = Field(default="manual", min_length=1, max_length=255)


class RunSnapshotV1(BaseModel):
    schema_version: Literal["run-snapshot/v1"]
    id: str
    run_id: str
    task_id: str
    event_sequence: int
    event_hash: str
    workflow_version: str
    run_state: dict[str, Any]
    step_state: list[dict[str, Any]]
    execution_plan_summary: dict[str, Any] | None
    context_summary: dict[str, Any]
    checksum: str
    parent_snapshot_id: str | None
    creation_reason: str
    created_at: datetime

    model_config = {"from_attributes": True}


class SnapshotRestoreV1(BaseModel):
    schema_version: Literal["snapshot-restore/v1"] = "snapshot-restore/v1"
    snapshot_id: str
    reason: str = Field(default="operator-requested restore", min_length=1)


class SnapshotRestoreResultV1(BaseModel):
    schema_version: Literal["snapshot-restore-result/v1"] = (
        "snapshot-restore-result/v1"
    )
    run_id: str
    snapshot_id: str
    status: str
    restored_step_ids: list[str]
    resumed: bool = False


class ReplayOverridesV1(BaseModel):
    model: str | None = None
    provider: str | None = None
    framework: str | None = None


class SnapshotReplayV1(BaseModel):
    schema_version: Literal["snapshot-replay/v1"] = "snapshot-replay/v1"
    snapshot_id: str
    reason: str = Field(min_length=1)
    overrides: ReplayOverridesV1 = Field(default_factory=ReplayOverridesV1)


class ReplayCreatedV1(BaseModel):
    schema_version: Literal["run-replay/v1"] = "run-replay/v1"
    replay_id: str
    source_run_id: str
    source_snapshot_id: str
    replay_run_id: str
    replay_reason: str
    overrides: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class ReplayComparisonV1(BaseModel):
    schema_version: Literal["replay-comparison/v1"] = "replay-comparison/v1"
    replay_id: str
    source_run_id: str
    source_snapshot_id: str
    replay_run_id: str
    replay_reason: str
    overrides: dict[str, Any]
    comparison_status: str
    source: dict[str, Any]
    replay: dict[str, Any]

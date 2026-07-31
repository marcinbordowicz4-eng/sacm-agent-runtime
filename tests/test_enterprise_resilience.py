import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from sacm.core.execution_plane_service import ExecutionPlaneService, _utcnow
from sacm.core.resilience_service import (
    BackupService,
    OperationalHealthService,
    build_pg_dump_command,
    error_budget,
)
from sacm.core.tenancy_service import TenancyService
from sacm.infrastructure.db.models import BackupRecord, Base, ExecutionJob, RunStep
from sacm.infrastructure.db.session import get_db
from sacm.schemas.resilience import BackupCreate
from tests.test_execution_plane_service import _executor, _job, _project


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        command = list(command)
        self.calls.append((command, kwargs))
        if Path(command[0]).name == "pg_dump":
            destination = Path(command[command.index("--file") + 1])
            destination.write_bytes(b"safe logical backup")
        return subprocess.CompletedProcess(command, 0, "", "")


def test_backup_command_uses_argument_array_and_hides_credentials(
    db, tmp_path, monkeypatch
):
    root = tmp_path / "backups"
    destination = root / "backup.dump"
    password_file = tmp_path / "pg-password"
    password_file.write_text("do-not-log", encoding="utf-8")
    monkeypatch.setenv("SACM_BACKUP_ROOT", str(root))
    monkeypatch.setenv("SACM_BACKUP_DB_HOST", "db.internal")
    monkeypatch.setenv("SACM_BACKUP_DB_USER", "backup")
    monkeypatch.setenv("SACM_BACKUP_DB_PASSWORD_FILE", str(password_file))
    runner = RecordingRunner()

    record = BackupService(db, runner).create(
        BackupCreate(
            source_database="sacm",
            storage_uri=destination.as_uri(),
            rpo_target_seconds=3600,
            rto_target_seconds=1800,
            execute=True,
        ),
        "platform-admin",
    )

    command, kwargs = runner.calls[0]
    assert command == build_pg_dump_command("sacm", destination)
    assert kwargs["shell"] is False
    assert "do-not-log" not in " ".join(command)
    assert kwargs["env"]["PGPASSWORD"] == "do-not-log"
    assert record.status == "COMPLETED"
    assert record.checksum


def test_restore_requires_guard_and_checksum_failure_is_recorded(
    db, tmp_path, monkeypatch
):
    root = tmp_path / "backups"
    root.mkdir()
    source = root / "backup.dump"
    source.write_bytes(b"original")
    monkeypatch.setenv("SACM_BACKUP_ROOT", str(root))
    monkeypatch.setenv("SACM_DESTRUCTIVE_RESTORE_GUARD", "explicit-guard-token")
    backup = BackupRecord(
        scope_type="GLOBAL",
        source_database="sacm",
        storage_uri=source.as_uri(),
        status="COMPLETED",
        checksum="0" * 64,
        encryption_metadata={"algorithm": "none"},
        artifact_metadata={},
        evidence_metadata={},
        rpo_target_seconds=3600,
        rto_target_seconds=1800,
        snapshot_at=_utcnow(),
        requested_by="platform-admin",
        completed_at=_utcnow(),
    )
    db.add(backup)
    db.commit()
    service = BackupService(db, RecordingRunner())

    with pytest.raises(PermissionError, match="guard token"):
        service.verify_restore(
            backup.id,
            "platform-admin",
            destructive_restore=True,
            target_database="production",
        )

    drill = service.verify_restore(backup.id, "platform-admin")
    assert drill.status == "FAILED"
    assert drill.checks["checksum"] is False
    assert db.get(BackupRecord, backup.id).status == "CORRUPT"


def test_stale_backup_degrades_operational_health(db, monkeypatch):
    monkeypatch.setenv("SACM_BACKUP_MAX_AGE_SECONDS", "60")
    monkeypatch.setenv("SACM_JOB_SIGNING_PRIVATE_KEY", "configured")
    monkeypatch.setenv("SACM_JOB_SIGNING_KEY_ID", "job-v1")
    backup = BackupRecord(
        scope_type="GLOBAL",
        source_database="sacm",
        storage_uri="file:///var/lib/sacm/backups/backup.dump",
        status="COMPLETED",
        checksum="a" * 64,
        encryption_metadata={"algorithm": "age"},
        artifact_metadata={},
        evidence_metadata={},
        rpo_target_seconds=60,
        rto_target_seconds=60,
        requested_by="platform-admin",
        completed_at=_utcnow() - timedelta(seconds=61),
    )
    db.add(backup)
    db.commit()

    health = OperationalHealthService(db).inspect(persist=False)

    assert health["backup"]["status"] == "UNHEALTHY"
    assert health["backup"]["freshness_seconds"] >= 60
    assert health["status"] == "UNHEALTHY"


def test_orphan_recovery_dead_letters_and_reconciles_running_step(db):
    _, project = _project(db, "owner", "resilience")
    executor, _, _ = _executor(db, project.id, "owner", "executor")
    _, step, job = _job(db, project.id)
    job.max_attempts = 1
    db.commit()
    service = ExecutionPlaneService(db)
    lease = service.acquire_lease(executor, lease_seconds=15)
    assert lease is not None
    service.start_job(executor, job.id, lease.lease_token)
    lease.job.lease_expires_at = _utcnow() - timedelta(seconds=1)
    db.commit()

    report = service.recover_orphaned()

    assert report["dead_lettered"] == 1
    assert db.get(ExecutionJob, job.id).state == "DEAD_LETTER"
    assert db.get(RunStep, step.id).status == "FAILED"


def test_slo_error_budget_math():
    allowed, remaining, observed, status = error_budget(99.0, 1000, 5)
    assert allowed == pytest.approx(10)
    assert remaining == pytest.approx(5)
    assert observed == pytest.approx(99.5)
    assert status == "HEALTHY"

    assert error_budget(99.9, 0, 0)[2:] == (None, "UNKNOWN")


def test_resilience_api_authentication_and_tenant_scope(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    tenancy = TenancyService(db)
    org_a = tenancy.create_organization("alpha-resilience", "Alpha", "owner-a")
    tenancy.create_organization("bravo-resilience", "Bravo", "owner-b")
    root = tmp_path / "backups"
    monkeypatch.setenv("SACM_BACKUP_ROOT", str(root))

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            assert client.get(
                "/v1/backups", params={"organization_id": org_a.id}
            ).status_code == 401
            created = client.post(
                "/v1/backups",
                headers={"X-SACM-Actor": "owner-a"},
                json={
                    "organization_id": org_a.id,
                    "source_database": "sacm",
                    "storage_uri": (root / "tenant.dump").as_uri(),
                    "rpo_target_seconds": 3600,
                    "rto_target_seconds": 1800,
                },
            )
            assert created.status_code == 201
            denied = client.get(
                "/v1/backups",
                headers={"X-SACM-Actor": "owner-b"},
                params={"organization_id": org_a.id},
            )
            assert denied.status_code == 403
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()

from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from sacm.core.auth_service import production_mode
from sacm.core.governance_service import ResidencyService, governance_health
from sacm.core.observability import OpenTelemetryService
from sacm.core.tenancy_service import TenancyService
from sacm.infrastructure.db.models import (
    BackupRecord,
    DisasterRecoveryDrill,
    ExecutionJob,
    ExecutorRegistration,
    GovernanceRequest,
    OperationalHealthSnapshot,
    RunOutcomeAnalytics,
    SIEMDelivery,
    SLOContract,
    SLOEvaluation,
    TenantAuditEvent,
)
from sacm.schemas.resilience import BackupCreate, SLOContractUpsert

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _metrics() -> OpenTelemetryService:
    return OpenTelemetryService(
        os.getenv("SACM_OTEL_ENABLED", "false").lower() == "true"
    )


def _secret_value(name: str) -> str | None:
    file_path = os.getenv(f"{name}_FILE")
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    return os.getenv(name)


def _safe_storage_path(storage_uri: str) -> Path:
    parsed = urlsplit(storage_uri)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Storage URI must not contain credentials, query, or fragment.")
    if parsed.scheme not in {"", "file"} or parsed.netloc not in {"", "localhost"}:
        raise ValueError("Only credential-free file storage URIs are supported.")
    path = Path(unquote(parsed.path if parsed.scheme else storage_uri)).resolve()
    root = Path(os.getenv("SACM_BACKUP_ROOT", "/var/lib/sacm/backups")).resolve()
    if path != root and root not in path.parents:
        raise ValueError("Backup storage path must be inside SACM_BACKUP_ROOT.")
    return path


def _credential_environment(prefix: str) -> dict[str, str]:
    env = os.environ.copy()
    password_file = os.getenv(f"{prefix}_PASSWORD_FILE")
    if password_file:
        env["PGPASSWORD"] = Path(password_file).read_text(encoding="utf-8").strip()
    elif os.getenv(f"{prefix}_PASSWORD"):
        env["PGPASSWORD"] = os.environ[f"{prefix}_PASSWORD"]
    pgpass_file = os.getenv(f"{prefix}_PGPASSFILE")
    if pgpass_file:
        env["PGPASSFILE"] = pgpass_file
    return env


def postgres_arguments(prefix: str, database: str) -> list[str]:
    arguments: list[str] = []
    values = (
        ("--host", os.getenv(f"{prefix}_HOST")),
        ("--port", os.getenv(f"{prefix}_PORT")),
        ("--username", os.getenv(f"{prefix}_USER")),
        ("--dbname", database),
    )
    for flag, value in values:
        if value:
            arguments.extend([flag, value])
    return arguments


def build_pg_dump_command(database: str, destination: Path) -> list[str]:
    return [
        os.getenv("SACM_PG_DUMP_BIN", "pg_dump"),
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--file",
        str(destination),
        *postgres_arguments("SACM_BACKUP_DB", database),
    ]


def build_pg_restore_command(database: str, source: Path, *, clean: bool) -> list[str]:
    command = [
        os.getenv("SACM_PG_RESTORE_BIN", "pg_restore"),
        "--exit-on-error",
        "--no-owner",
        "--no-acl",
    ]
    if clean:
        command.extend(["--clean", "--if-exists"])
    return [
        *command,
        *postgres_arguments("SACM_RESTORE_DB", database),
        str(source),
    ]


def _run(
    runner: CommandRunner,
    command: Sequence[str],
    *,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return runner(
        list(command),
        check=True,
        capture_output=True,
        text=True,
        env=env,
        shell=False,
    )


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BackupService:
    def __init__(self, db: Session, runner: CommandRunner = subprocess.run) -> None:
        self.db = db
        self.runner = runner

    def create(self, payload: BackupCreate, actor: str) -> BackupRecord:
        self._authorize(payload.organization_id, actor, "resilience.manage")
        path = _safe_storage_path(payload.storage_uri)
        if path.exists() and path.is_dir():
            raise ValueError("Backup storage URI must identify a file.")
        encryption = dict(payload.encryption_metadata)
        recipients_file = os.getenv("SACM_BACKUP_AGE_RECIPIENTS_FILE")
        if recipients_file:
            encryption = {
                **encryption,
                "algorithm": "age",
                "recipients_source": "file",
                "key_id": os.getenv("SACM_BACKUP_ENCRYPTION_KEY_ID"),
            }
        else:
            encryption.setdefault("algorithm", "none")
        if production_mode() and encryption["algorithm"] == "none":
            raise ValueError("Production logical backups must be encrypted.")
        storage = (
            ResidencyService(self.db).resolve(
                organization_id=payload.organization_id,
                project_id=None,
                category="backups",
                region=payload.storage_region,
                classification=payload.storage_classification,
                storage_class=payload.storage_class,
            )
            if payload.organization_id
            else {
                "region": payload.storage_region,
                "classification": payload.storage_classification,
                "storage_class": payload.storage_class,
            }
        )
        record = BackupRecord(
            id=str(uuid.uuid4()),
            scope_type="ORGANIZATION" if payload.organization_id else "GLOBAL",
            organization_id=payload.organization_id,
            storage_region=storage.get("region"),
            storage_classification=storage.get("classification"),
            storage_class=storage.get("storage_class"),
            source_database=payload.source_database,
            storage_uri=payload.storage_uri,
            status="PENDING",
            encryption_metadata=encryption,
            artifact_metadata=payload.artifact_metadata,
            evidence_metadata=payload.evidence_metadata,
            rpo_target_seconds=payload.rpo_target_seconds,
            rto_target_seconds=payload.rto_target_seconds,
            requested_by=actor,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        if payload.execute:
            return self.execute(record.id, actor)
        return record

    def execute(self, backup_id: str, actor: str) -> BackupRecord:
        record = self._get_authorized(backup_id, actor, "resilience.manage")
        if record.status not in {"PENDING", "FAILED"}:
            raise ValueError(f"Backup cannot execute from {record.status}.")
        destination = _safe_storage_path(record.storage_uri)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw_destination = destination
        encrypted = record.encryption_metadata.get("algorithm") == "age"
        if encrypted:
            raw_destination = destination.with_name(f".{destination.name}.{record.id}.raw")
        record.status = "RUNNING"
        record.started_at = _utcnow()
        record.failure = None
        self.db.commit()
        try:
            _run(
                self.runner,
                build_pg_dump_command(record.source_database, raw_destination),
                env=_credential_environment("SACM_BACKUP_DB"),
            )
            if encrypted:
                recipients_file = os.getenv("SACM_BACKUP_AGE_RECIPIENTS_FILE")
                if not recipients_file:
                    raise ValueError("age recipients file is not configured.")
                _run(
                    self.runner,
                    [
                        os.getenv("SACM_AGE_BIN", "age"),
                        "--encrypt",
                        "--recipients-file",
                        recipients_file,
                        "--output",
                        str(destination),
                        str(raw_destination),
                    ],
                    env=os.environ.copy(),
                )
            record.checksum = _checksum(destination)
            record.size_bytes = destination.stat().st_size
            record.snapshot_at = record.started_at
            record.completed_at = _utcnow()
            record.status = "COMPLETED"
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            record.status = "FAILED"
            record.failure = {"type": type(exc).__name__, "message": "Backup command failed."}
        finally:
            if encrypted and raw_destination.exists():
                raw_destination.unlink()
            self.db.commit()
            self.db.refresh(record)
        _metrics().record_resilience_event(
            "backup_operation",
            attributes={"status": record.status, "scope": record.scope_type},
        )
        return record

    def list(self, actor: str, organization_id: str | None) -> list[BackupRecord]:
        self._authorize(organization_id, actor, "resilience.read")
        query = self.db.query(BackupRecord)
        if organization_id:
            query = query.filter(BackupRecord.organization_id == organization_id)
        else:
            query = query.filter(BackupRecord.organization_id.is_(None))
        return query.order_by(BackupRecord.created_at.desc()).all()

    def verify_restore(
        self,
        backup_id: str,
        actor: str,
        *,
        destructive_restore: bool = False,
        target_database: str | None = None,
        guard_token: str | None = None,
        keep_isolated_database: bool = False,
    ) -> DisasterRecoveryDrill:
        backup = self._get_authorized(backup_id, actor, "resilience.manage")
        if backup.status != "COMPLETED" or not backup.checksum:
            raise ValueError("Only completed backups with checksums can be restored.")
        expected_guard = _secret_value("SACM_DESTRUCTIVE_RESTORE_GUARD")
        if destructive_restore and (
            not expected_guard
            or not guard_token
            or not hashlib.sha256(guard_token.encode()).digest()
            == hashlib.sha256(expected_guard.encode()).digest()
        ):
            raise PermissionError(
                "Destructive restore requires the configured explicit guard token."
            )
        if destructive_restore and not target_database:
            raise ValueError("Destructive restore requires target_database.")
        isolated = not destructive_restore
        target = target_database or f"sacm_dr_{uuid.uuid4().hex[:16]}"
        drill = DisasterRecoveryDrill(
            id=str(uuid.uuid4()),
            backup_id=backup.id,
            organization_id=backup.organization_id,
            status="RUNNING",
            target_database=target,
            isolated_target=isolated,
            destructive_restore=destructive_restore,
            checks={},
            requested_by=actor,
            started_at=_utcnow(),
        )
        self.db.add(drill)
        self.db.commit()
        source = _safe_storage_path(backup.storage_uri)
        decrypted_source: Path | None = None
        created = False
        checks: dict[str, Any] = {"checksum": False}
        try:
            if not source.is_file() or _checksum(source) != backup.checksum:
                backup.status = "CORRUPT"
                raise ValueError("Backup checksum verification failed.")
            checks["checksum"] = True
            restore_source = source
            if backup.encryption_metadata.get("algorithm") == "age":
                identity_file = os.getenv("SACM_BACKUP_AGE_IDENTITY_FILE")
                if not identity_file:
                    raise ValueError("age identity file is not configured.")
                decrypted_source = source.with_name(f".{source.name}.{drill.id}.restore")
                _run(
                    self.runner,
                    [
                        os.getenv("SACM_AGE_BIN", "age"),
                        "--decrypt",
                        "--identity",
                        identity_file,
                        "--output",
                        str(decrypted_source),
                        str(source),
                    ],
                    env=os.environ.copy(),
                )
                restore_source = decrypted_source
            env = _credential_environment("SACM_RESTORE_DB")
            if isolated:
                _run(
                    self.runner,
                    [
                        os.getenv("SACM_CREATEDB_BIN", "createdb"),
                        *postgres_arguments("SACM_RESTORE_DB", target),
                    ],
                    env=env,
                )
                created = True
            _run(
                self.runner,
                build_pg_restore_command(target, restore_source, clean=destructive_restore),
                env=env,
            )
            checks["restore"] = True
            checks.update(self._database_checks(target, env))
            drill.status = "PASSED" if all(checks.values()) else "FAILED"
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            drill.status = "FAILED"
            drill.failure = {
                "type": type(exc).__name__,
                "message": (
                    "Backup checksum verification failed."
                    if "checksum" in str(exc).lower()
                    else "Restore verification failed."
                ),
            }
        finally:
            if isolated and created and not keep_isolated_database:
                try:
                    _run(
                        self.runner,
                        [
                            os.getenv("SACM_DROPDB_BIN", "dropdb"),
                            "--if-exists",
                            *postgres_arguments("SACM_RESTORE_DB", target),
                        ],
                        env=_credential_environment("SACM_RESTORE_DB"),
                    )
                except (OSError, subprocess.SubprocessError):
                    checks["cleanup"] = False
                    drill.status = "FAILED"
            if decrypted_source and decrypted_source.exists():
                decrypted_source.unlink()
            now = _utcnow()
            drill.checks = checks
            drill.completed_at = now
            drill.measured_rto_seconds = max(
                0.0, (now - (drill.started_at or now)).total_seconds()
            )
            snapshot_at = backup.snapshot_at or backup.completed_at or backup.created_at
            drill.measured_rpo_seconds = max(
                0.0, ((drill.started_at or now) - snapshot_at).total_seconds()
            )
            self.db.commit()
            self.db.refresh(drill)
        _metrics().record_resilience_event(
            "dr_duration",
            drill.measured_rto_seconds or 0.0,
            {"status": drill.status, "isolated": drill.isolated_target},
        )
        return drill

    def _database_checks(
        self, database: str, env: dict[str, str]
    ) -> dict[str, bool]:
        checks = {
            "readiness": "SELECT 1",
            "schema": (
                "SELECT CASE WHEN count(*) > 0 THEN 1 ELSE 0 END "
                "FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog','information_schema')"
            ),
            "integrity": (
                "SELECT CASE WHEN count(*) = 0 THEN 1 ELSE 0 END "
                "FROM pg_constraint WHERE contype = 'f' AND NOT convalidated"
            ),
        }
        results: dict[str, bool] = {}
        for name, statement in checks.items():
            _run(
                self.runner,
                [
                    os.getenv("SACM_PSQL_BIN", "psql"),
                    "--no-psqlrc",
                    "--tuples-only",
                    "--set",
                    "ON_ERROR_STOP=1",
                    *postgres_arguments("SACM_RESTORE_DB", database),
                    "--command",
                    statement,
                ],
                env=env,
            )
            results[name] = True
        return results

    def _get_authorized(
        self, backup_id: str, actor: str, permission: str
    ) -> BackupRecord:
        record = self.db.get(BackupRecord, backup_id)
        if record is None:
            raise ValueError("Backup not found.")
        self._authorize(record.organization_id, actor, permission)
        return record

    @staticmethod
    def _platform_admin(actor: str) -> bool:
        configured = {
            item.strip()
            for item in os.getenv("SACM_PLATFORM_ADMIN_SUBJECTS", "").split(",")
            if item.strip()
        }
        return actor in configured or (not production_mode() and not configured)

    def _authorize(
        self, organization_id: str | None, actor: str, permission: str
    ) -> None:
        if organization_id:
            TenancyService(self.db).require_permission(
                organization_id,
                actor,
                permission,
                resource_type="resilience",
                resource_id=organization_id,
            )
        elif not self._platform_admin(actor):
            raise PermissionError("Global resilience operations require a platform admin.")


def error_budget(
    objective_percent: float, total_events: int, bad_events: int
) -> tuple[float, float, float | None, str]:
    if total_events <= 0:
        return 0.0, 0.0, None, "UNKNOWN"
    allowed = total_events * (1.0 - objective_percent / 100.0)
    remaining = allowed - bad_events
    observed = 100.0 * (total_events - bad_events) / total_events
    return allowed, remaining, observed, (
        "HEALTHY" if observed >= objective_percent else "BREACHED"
    )


class SLOService:
    DEFAULTS: tuple[tuple[str, float, float | None, str], ...] = (
        ("availability", 99.9, None, "Operational health is available."),
        ("job_start_latency", 99.0, 300.0, "Queued jobs start within threshold."),
        ("completion_rate", 99.0, None, "Terminal jobs complete successfully."),
        ("evidence_coverage", 95.0, None, "Run evidence coverage meets target."),
        ("audit_delivery", 100.0, None, "Tenant audit chain remains deliverable."),
        (
            "governance_backlog",
            100.0,
            25.0,
            "Open governance request backlog remains within threshold.",
        ),
        ("backup_freshness", 100.0, 86400.0, "A verified backup is fresh."),
        ("rpo", 100.0, 86400.0, "Measured RPO meets target."),
        ("rto", 100.0, 3600.0, "Measured RTO meets target."),
    )

    def __init__(self, db: Session) -> None:
        self.db = db

    def ensure_defaults(
        self, actor: str, organization_id: str | None = None
    ) -> list[SLOContract]:
        self._authorize(organization_id, actor, "resilience.manage")
        scope_key = self._scope_key(organization_id)
        for metric, objective, threshold, description in self.DEFAULTS:
            existing = self.db.query(SLOContract).filter_by(
                scope_key=scope_key, metric=metric
            ).first()
            if existing is None:
                self.db.add(
                    SLOContract(
                        id=str(uuid.uuid4()),
                        scope_key=scope_key,
                        organization_id=organization_id,
                        metric=metric,
                        objective_percent=objective,
                        threshold_seconds=threshold,
                        window_seconds=30 * 24 * 3600,
                        description=description,
                        created_by=actor,
                    )
                )
        self.db.commit()
        return self.list_contracts(actor, organization_id)

    def upsert(
        self, actor: str, payload: SLOContractUpsert, organization_id: str | None
    ) -> SLOContract:
        self._authorize(organization_id, actor, "resilience.manage")
        scope_key = self._scope_key(organization_id)
        contract = self.db.query(SLOContract).filter_by(
            scope_key=scope_key, metric=payload.metric
        ).first()
        if contract is None:
            contract = SLOContract(
                id=str(uuid.uuid4()),
                scope_key=scope_key,
                organization_id=organization_id,
                metric=payload.metric,
                created_by=actor,
            )
            self.db.add(contract)
        for key, value in payload.model_dump().items():
            setattr(contract, key, value)
        self.db.commit()
        self.db.refresh(contract)
        return contract

    def list_contracts(
        self, actor: str, organization_id: str | None
    ) -> list[SLOContract]:
        self._authorize(organization_id, actor, "resilience.read")
        return self.db.query(SLOContract).filter_by(
            scope_key=self._scope_key(organization_id)
        ).order_by(SLOContract.metric).all()

    def evaluate(
        self, actor: str, organization_id: str | None
    ) -> list[SLOEvaluation]:
        contracts = self.list_contracts(actor, organization_id)
        now = _utcnow()
        evaluations: list[SLOEvaluation] = []
        for contract in contracts:
            start = now - timedelta(seconds=contract.window_seconds)
            total, bad, details = self._measure(contract, start, now)
            allowed, remaining, observed, status = error_budget(
                contract.objective_percent, total, bad
            )
            evaluation = SLOEvaluation(
                id=str(uuid.uuid4()),
                contract_id=contract.id,
                organization_id=organization_id,
                metric=contract.metric,
                status=status,
                observed_percent=observed,
                total_events=total,
                bad_events=bad,
                error_budget_allowed=allowed,
                error_budget_remaining=remaining,
                details=details,
                window_started_at=start,
                window_ended_at=now,
            )
            self.db.add(evaluation)
            evaluations.append(evaluation)
            _metrics().record_resilience_event(
                "slo_evaluation",
                attributes={"metric": contract.metric, "status": status},
            )
        self.db.commit()
        for evaluation in evaluations:
            self.db.refresh(evaluation)
        return evaluations

    def _measure(
        self, contract: SLOContract, start: datetime, now: datetime
    ) -> tuple[int, int, dict[str, Any]]:
        org = contract.organization_id
        metric = contract.metric
        threshold = contract.threshold_seconds
        if metric == "availability":
            health_query = self.db.query(OperationalHealthSnapshot).filter(
                OperationalHealthSnapshot.created_at >= start
            )
            if org:
                health_query = health_query.filter(
                    OperationalHealthSnapshot.organization_id == org
                )
            health_rows = health_query.all()
            return len(health_rows), sum(
                row.status != "HEALTHY" for row in health_rows
            ), {}
        if metric == "job_start_latency":
            job_query = self.db.query(ExecutionJob).filter(
                ExecutionJob.queued_at >= start, ExecutionJob.started_at.is_not(None)
            )
            if org:
                job_query = job_query.filter(ExecutionJob.organization_id == org)
            job_rows = job_query.all()
            latencies = [
                max(0.0, (row.started_at - row.queued_at).total_seconds())
                for row in job_rows
                if row.started_at
            ]
            return len(latencies), sum(
                value > float(threshold or 0) for value in latencies
            ), {"threshold_seconds": threshold}
        if metric == "completion_rate":
            completion_query = self.db.query(ExecutionJob).filter(
                ExecutionJob.created_at >= start,
                ExecutionJob.state.in_(
                    ("COMPLETED", "FAILED", "EXPIRED", "CANCELLED", "DEAD_LETTER")
                ),
            )
            if org:
                completion_query = completion_query.filter(
                    ExecutionJob.organization_id == org
                )
            completion_rows = completion_query.all()
            return len(completion_rows), sum(
                row.state != "COMPLETED" for row in completion_rows
            ), {}
        if metric == "evidence_coverage":
            analytics_query = self.db.query(RunOutcomeAnalytics).filter(
                RunOutcomeAnalytics.computed_at >= start
            )
            if org:
                analytics_query = analytics_query.join(
                    ExecutionJob, ExecutionJob.run_id == RunOutcomeAnalytics.run_id
                ).filter(ExecutionJob.organization_id == org)
            analytics_rows = analytics_query.distinct().all()
            values = [
                float(row.evidence_coverage_percent)
                for row in analytics_rows
                if row.evidence_coverage_percent is not None
            ]
            return len(values), sum(
                value < contract.objective_percent for value in values
            ), {"average_percent": sum(values) / len(values) if values else None}
        if metric == "audit_delivery":
            audit_query = self.db.query(TenantAuditEvent).filter(
                TenantAuditEvent.created_at >= start
            )
            if org:
                audit_query = audit_query.filter(
                    TenantAuditEvent.organization_id == org
                )
            audit_rows = audit_query.order_by(
                TenantAuditEvent.organization_id, TenantAuditEvent.sequence
            ).all()
            bad = 0
            previous: dict[str, str | None] = {}
            for row in audit_rows:
                scope = row.organization_id or "global"
                if row.previous_event_hash != previous.get(scope):
                    bad += 1
                previous[scope] = row.event_hash
            delivery_query = self.db.query(SIEMDelivery).filter(
                SIEMDelivery.created_at >= start
            )
            if org:
                delivery_query = delivery_query.filter(
                    SIEMDelivery.organization_id == org
                )
            deliveries = delivery_query.all()
            delivery_bad = sum(
                delivery.status != "DELIVERED" for delivery in deliveries
            )
            return len(audit_rows) + len(deliveries), bad + delivery_bad, {
                "chain_contiguous": bad == 0,
                "siem_deliveries": len(deliveries),
                "siem_delivery_failures": delivery_bad,
            }
        if metric == "governance_backlog":
            backlog_query = self.db.query(GovernanceRequest).filter(
                GovernanceRequest.status.in_(
                    [
                        "PENDING_APPROVAL",
                        "APPROVED",
                        "INVENTORIED",
                        "PROCESSING",
                        "BLOCKED",
                    ]
                )
            )
            if org:
                backlog_query = backlog_query.filter(
                    GovernanceRequest.organization_id == org
                )
            backlog = backlog_query.count()
            return 1, int(backlog > int(threshold or 0)), {
                "backlog": backlog,
                "threshold": int(threshold or 0),
            }
        if metric == "backup_freshness":
            backup_query = self.db.query(BackupRecord).filter(
                BackupRecord.status == "COMPLETED"
            )
            backup_query = backup_query.filter(
                BackupRecord.organization_id == org
                if org
                else BackupRecord.organization_id.is_(None)
            )
            latest = backup_query.order_by(BackupRecord.completed_at.desc()).first()
            if latest is None or latest.completed_at is None:
                return 1, 1, {"freshness_seconds": None, "threshold_seconds": threshold}
            freshness = max(0.0, (now - latest.completed_at).total_seconds())
            return 1, int(freshness > float(threshold or 0)), {
                "freshness_seconds": freshness,
                "threshold_seconds": threshold,
            }
        if metric in {"rpo", "rto"}:
            drill_query = self.db.query(DisasterRecoveryDrill).filter(
                DisasterRecoveryDrill.completed_at >= start,
                DisasterRecoveryDrill.status.in_(("PASSED", "FAILED")),
            )
            if org:
                drill_query = drill_query.filter(
                    DisasterRecoveryDrill.organization_id == org
                )
            drill_rows = drill_query.all()
            drill_values: list[float | None] = [
                (
                    row.measured_rpo_seconds
                    if metric == "rpo"
                    else row.measured_rto_seconds
                )
                for row in drill_rows
            ]
            usable = [value for value in drill_values if value is not None]
            return len(usable), sum(
                value > float(threshold or 0) for value in usable
            ), {"threshold_seconds": threshold}
        return 0, 0, {}

    @staticmethod
    def _scope_key(organization_id: str | None) -> str:
        return f"organization:{organization_id}" if organization_id else "global"

    def _authorize(
        self, organization_id: str | None, actor: str, permission: str
    ) -> None:
        BackupService(self.db)._authorize(organization_id, actor, permission)


class OperationalHealthService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def inspect(
        self, organization_id: str | None = None, *, persist: bool = True
    ) -> dict[str, Any]:
        now = _utcnow()
        checks: dict[str, Any] = {}
        try:
            self.db.execute(func.now().select())
            checks["database"] = {"status": "HEALTHY"}
        except Exception:
            checks["database"] = {"status": "UNHEALTHY"}
        checks["redis"] = self._redis_check()
        checks["opa"] = self._opa_check()
        active_query = self.db.query(ExecutorRegistration).filter(
            ExecutorRegistration.status == "ACTIVE"
        )
        queue_query = self.db.query(ExecutionJob).filter(
            ExecutionJob.state == "QUEUED"
        )
        backup_query = self.db.query(BackupRecord).filter(
            BackupRecord.status == "COMPLETED"
        )
        if organization_id:
            active_query = active_query.filter(
                ExecutorRegistration.organization_id == organization_id
            )
            queue_query = queue_query.filter(
                ExecutionJob.organization_id == organization_id
            )
            backup_query = backup_query.filter(
                BackupRecord.organization_id == organization_id
            )
        else:
            backup_query = backup_query.filter(BackupRecord.organization_id.is_(None))
        active = active_query.count()
        queued = queue_query.count()
        oldest = queue_query.order_by(ExecutionJob.queued_at).first()
        oldest_age = max(
            0.0, (now - oldest.queued_at).total_seconds()
        ) if oldest else 0.0
        latest_backup = backup_query.order_by(BackupRecord.completed_at.desc()).first()
        max_age = int(os.getenv("SACM_BACKUP_MAX_AGE_SECONDS", "86400"))
        backup_age = (
            max(0.0, (now - latest_backup.completed_at).total_seconds())
            if latest_backup and latest_backup.completed_at
            else None
        )
        backup_status = (
            "HEALTHY"
            if backup_age is not None and backup_age <= max_age
            else "UNHEALTHY"
        )
        audit = self._audit_health(organization_id)
        governance = governance_health(self.db, organization_id)
        signing = {
            "status": (
                "HEALTHY"
                if os.getenv("SACM_JOB_SIGNING_PRIVATE_KEY")
                or os.getenv("SACM_JOB_SIGNING_PRIVATE_KEY_FILE")
                else "UNHEALTHY"
            ),
            "key_id_configured": bool(
                os.getenv("SACM_JOB_SIGNING_KEY_ID")
                or os.getenv("SACM_EVIDENCE_SIGNING_KEY_ID")
            ),
        }
        if queued and active == 0:
            executor_status = "UNHEALTHY"
        elif active == 0:
            executor_status = "DEGRADED"
        else:
            executor_status = "HEALTHY"
        required_statuses = [
            checks["database"]["status"],
            backup_status,
            audit["status"],
            governance["status"],
            signing["status"],
            executor_status,
        ]
        optional_statuses = [
            item["status"]
            for item in (checks["redis"], checks["opa"])
            if item["required"]
        ]
        statuses = required_statuses + optional_statuses
        status = (
            "UNHEALTHY"
            if "UNHEALTHY" in statuses
            else "DEGRADED"
            if "DEGRADED" in statuses
            else "HEALTHY"
        )
        result = {
            "status": status,
            "generated_at": now,
            "checks": checks,
            "queue": {"depth": queued, "oldest_age_seconds": oldest_age},
            "executors": {"active": active, "status": executor_status},
            "backup": {
                "status": backup_status,
                "freshness_seconds": backup_age,
                "max_age_seconds": max_age,
            },
            "audit": audit,
            "governance": governance,
            "signing": signing,
        }
        if persist:
            self.db.add(
                OperationalHealthSnapshot(
                    id=str(uuid.uuid4()),
                    organization_id=organization_id,
                    status=status,
                    checks=result,
                    queue_depth=queued,
                    oldest_queue_age_seconds=oldest_age,
                    active_executor_count=active,
                )
            )
            self.db.commit()
        metrics = _metrics()
        metrics.record_resilience_event("queue_depth", queued)
        metrics.record_resilience_event("queue_age", oldest_age)
        metrics.record_resilience_event("executor_capacity", active)
        if backup_age is not None:
            metrics.record_resilience_event("backup_freshness", backup_age)
        metrics.record_resilience_event(
            "governance_backlog", governance["governance_backlog"]
        )
        metrics.record_resilience_event(
            "audit_delivery_backlog", governance["pending_deliveries"]
        )
        metrics.record_resilience_event(
            "audit_delivery_dead_letters",
            governance["dead_letter_deliveries"],
        )
        return result

    @staticmethod
    def _redis_check() -> dict[str, Any]:
        required = os.getenv("SACM_HEALTH_REDIS_REQUIRED", "false").lower() == "true"
        url = os.getenv("REDIS_URL")
        if not url:
            return {"status": "UNHEALTHY" if required else "SKIPPED", "required": required}
        try:
            import redis

            redis.Redis.from_url(
                url,
                socket_connect_timeout=1,
                socket_timeout=1,
            ).ping()
            return {"status": "HEALTHY", "required": required}
        except Exception:
            return {"status": "UNHEALTHY", "required": required}

    @staticmethod
    def _opa_check() -> dict[str, Any]:
        required = os.getenv("SACM_HEALTH_OPA_REQUIRED", "false").lower() == "true"
        url = os.getenv("SACM_OPA_URL")
        if not url:
            return {"status": "UNHEALTHY" if required else "SKIPPED", "required": required}
        try:
            response = httpx.get(f"{url.rstrip('/')}/health", timeout=1.0)
            return {
                "status": "HEALTHY" if response.is_success else "UNHEALTHY",
                "required": required,
            }
        except httpx.HTTPError:
            return {"status": "UNHEALTHY", "required": required}

    def _audit_health(self, organization_id: str | None) -> dict[str, Any]:
        query = self.db.query(TenantAuditEvent)
        if organization_id:
            query = query.filter(TenantAuditEvent.organization_id == organization_id)
        rows = query.order_by(
            TenantAuditEvent.organization_id, TenantAuditEvent.sequence
        ).all()
        previous: dict[str, tuple[int, str] | None] = {}
        for row in rows:
            prior = previous.get(row.organization_id)
            if prior is None:
                valid = row.sequence == 1 and row.previous_event_hash is None
            else:
                valid = row.sequence == prior[0] + 1 and row.previous_event_hash == prior[1]
            if not valid:
                return {"status": "UNHEALTHY", "chain_valid": False}
            previous[row.organization_id] = (row.sequence, row.event_hash)
        return {"status": "HEALTHY", "chain_valid": True, "events_checked": len(rows)}

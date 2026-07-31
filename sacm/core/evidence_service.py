import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.orm import Session

from sacm.core.cost_service import CostService
from sacm.core.governance_service import ResidencyService
from sacm.core.run_service import RunService
from sacm.core.snapshot_service import SnapshotService
from sacm.core.supply_chain_service import (
    SupplyChainService,
    canonical_sha256,
    load_evidence_hmac_key,
)
from sacm.core.tenancy_service import (
    ResourceAuthorizationService,
    TenancyService,
    TenantContext,
)
from sacm.core.traceability_service import TraceabilityService
from sacm.infrastructure.db.models import (
    Approval,
    Artifact,
    ContextEvent,
    EvidencePack,
    ExecutionPlan,
    SupplyChainRecord,
)
from sacm.schemas.supply_chain import VerificationResultV1

_SECRET_KEY = re.compile(
    r"(?:^|_)(?:api_?key|authorization|credential|password|private_?key|secret|token)(?:$|_)",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?im)\b(api[_-]?key|authorization|credential|password|private[_-]?key|secret|token)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)


class EvidenceService:
    """Builds a hash-checked pack from recorded artifacts without fabricating evidence."""

    def __init__(self, db: Session, root: str | None = None) -> None:
        self.db = db
        self.runs = RunService(db)
        self.root = Path(
            root if root is not None else os.getenv("SACM_EVIDENCE_ROOT", ".sacm/evidence")
        )

    def build(
        self,
        run_id: str,
        actor_id: str | None = None,
        *,
        trusted_internal: bool = False,
    ) -> EvidencePack:
        run = self._authorize(
            run_id, actor_id, "evidence.build", trusted_internal=trusted_internal
        )
        context = ResourceAuthorizationService(self.db).run_context(run)
        existing_pack = (
            self.db.query(EvidencePack.id)
            .filter(EvidencePack.run_id == run_id)
            .first()
        )
        directory_name = run_id if existing_pack is None else f"{run_id}-{uuid.uuid4()}"
        directory = (self.root / directory_name).resolve()
        root = self.root.resolve()
        if directory.parent != root:
            raise ValueError("Evidence path must remain inside SACM_EVIDENCE_ROOT.")
        directory.mkdir(parents=True, exist_ok=True)

        self.runs._append_event(
            run,
            event_type="EvidencePackCreated",
            actor="system",
            payload={"path": str(directory)},
        )
        self.db.flush()
        snapshots = SnapshotService(self.db)
        if snapshots.available():
            snapshots.create(run.id, "evidence_pack_created")
        events = self.runs.events(run_id)
        steps = self.runs.list_steps(run_id)
        traceability = TraceabilityService(self.db).refresh(run.task_id)
        task_context = self._task_context(run)
        application_context = self._application_context(run)
        execution_plan = self._execution_plan(run)
        policy_security_approvals = self._policy_security_approvals(
            run, execution_plan
        )
        delivery_evidence = self._delivery_evidence(run)
        costs = CostService(self.db).summarize_task(run.task_id)
        artifact_inventory = self._artifact_inventory(run)
        supply_chain_records = self._supply_chain_records(run.id)
        supply_chain_completeness = SupplyChainService(self.db).completeness(run.id)
        signing = self._signing_metadata()
        manifest = {
            "schema_version": "run-manifest/v2",
            "evidence_pack_version": "2.0",
            "evidence_pack_schema_version": "evidence-pack/v2",
            "run_id": run.id,
            "task_id": run.task_id,
            "status": run.status,
            "workflow_version": run.workflow_version,
            "source_revision": run.source_revision,
            "event_chain_hash": events[-1].event_hash if events else None,
            "snapshot": (
                snapshots.latest_metadata(run.id) if snapshots.available() else None
            ),
            "replay": (
                snapshots.replay_metadata(run.id) if snapshots.available() else None
            ),
            "task": task_context,
            "readiness": task_context["readiness"],
            "application_context": application_context,
            "execution_plan": execution_plan,
            "policy_security_approvals": policy_security_approvals,
            "delivery": delivery_evidence,
            "usage_cost": costs,
            "traceability": traceability.model_dump(mode="json"),
            "artifacts": artifact_inventory,
            "supply_chain": {
                "records": supply_chain_records,
                "completeness": supply_chain_completeness.model_dump(mode="json"),
            },
            "event_chain": {
                "algorithm": "sha256",
                "event_count": len(events),
                "first_event_hash": events[0].event_hash if events else None,
                "last_event_hash": events[-1].event_hash if events else None,
                "sequences": [event.sequence for event in events],
            },
            "integrity": {
                "manifest_checksum_algorithm": "sha256",
                "checksums_file": "checksums.sha256",
                "signature": {
                    "present": signing["present"],
                    "algorithm": signing["algorithm"],
                    "key_id": signing["key_id"],
                    "path": (
                        "signature.sig"
                        if signing["present"]
                        else None
                    ),
                    "signed_file": "run-manifest.json",
                },
            },
        }
        manifest = self._sanitize(manifest)
        self._write_json(
            directory / "run-manifest.json",
            manifest,
        )
        self._write_json(
            directory / "request.json",
            {
                "title": run.task.title,
                "description": run.task.description,
                "task_contract": run.task.task_contract,
            },
        )
        self._write_json(
            directory / "steps.json",
            [
                {
                    "id": step.id,
                    "sequence": step.sequence,
                    "name": step.name,
                    "status": step.status,
                    "retry_count": step.retry_count,
                }
                for step in steps
            ],
        )
        with (directory / "events.jsonl").open("w", encoding="utf-8") as file:
            for event in events:
                file.write(
                    json.dumps(
                        {
                            "sequence": event.sequence,
                            "event_type": event.event_type,
                            "actor": event.actor,
                            "payload": self._sanitize(event.payload),
                            "event_hash": event.event_hash,
                            "previous_event_hash": event.previous_event_hash,
                            "occurred_at": event.occurred_at.isoformat(),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
        self._write_json(
            directory / "cost-report.json",
            costs,
        )
        agent_records = self._agent_records(run.task_id)
        self._write_recorded_artifacts(directory, agent_records)
        self._write_provenance(directory, run, manifest)
        self._write_external_artifacts(directory, run)
        signature = self._write_signature(directory)
        checksums = self._checksums(directory)
        (directory / "checksums.sha256").write_text(checksums, encoding="utf-8")
        manifest_hash = hashlib.sha256(
            (directory / "run-manifest.json").read_bytes()
        ).hexdigest()
        previous = (
            self.db.query(EvidencePack)
            .filter(EvidencePack.run_id == run.id)
            .order_by(EvidencePack.created_at.desc())
            .first()
        )
        previous_pack_hash = previous.pack_hash if previous else None
        pack_hash = canonical_sha256(
            {
                "manifest_hash": manifest_hash,
                "previous_pack_hash": previous_pack_hash,
                "signature": signature.get("signature") if signature else None,
            }
        )
        storage = (
            ResidencyService(self.db).resolve(
                organization_id=context.organization_id,
                project_id=context.project_id,
                category="evidence",
            )
            if context
            else {}
        )
        pack = EvidencePack(
            run_id=run.id,
            organization_id=context.organization_id if context else None,
            project_id=context.project_id if context else None,
            tenant_attribution=(
                {"schema_version": "tenant-attribution/v1", "source": context.source}
                if context
                else None
            ),
            storage_region=storage.get("region"),
            storage_classification=storage.get("classification"),
            storage_class=storage.get("storage_class"),
            path=str(directory),
            manifest_hash=manifest_hash,
            pack_hash=pack_hash,
            previous_pack_hash=previous_pack_hash,
            signature_algorithm=signature.get("algorithm") if signature else None,
            signature_key_id=signature.get("key_id") if signature else None,
            public_key_fingerprint=(
                signature.get("public_key_fingerprint") if signature else None
            ),
            public_key=signature.get("public_key") if signature else None,
            signature=signature.get("signature") if signature else None,
            verification_status="UNVERIFIED",
        )
        self.db.add(pack)
        self.db.flush()
        (
            self.db.query(SupplyChainRecord)
            .filter(
                SupplyChainRecord.run_id == run.id,
                SupplyChainRecord.evidence_pack_id.is_(None),
            )
            .update(
                {SupplyChainRecord.evidence_pack_id: pack.id},
                synchronize_session=False,
            )
        )
        self.db.commit()
        self.db.refresh(pack)
        TraceabilityService(self.db).refresh(run.task_id)
        if context and actor_id:
            TenancyService(self.db).audit_sensitive(
                context.organization_id,
                context.project_id,
                actor_id,
                "evidence.build",
                "evidence_pack",
                pack.id,
                "Evidence pack built.",
            )
        return pack

    def verify(
        self,
        run_id: str,
        evidence_id: str,
        actor_id: str | None = None,
        *,
        trusted_internal: bool = False,
    ) -> VerificationResultV1:
        self._authorize(
            run_id,
            actor_id,
            "evidence.read",
            evidence_id=evidence_id,
            trusted_internal=trusted_internal,
        )
        pack = (
            self.db.query(EvidencePack)
            .filter(
                EvidencePack.id == evidence_id,
                EvidencePack.run_id == run_id,
            )
            .first()
        )
        if pack is None:
            raise ValueError(f"Evidence pack {evidence_id} not found.")
        errors: list[str] = []
        directory = Path(pack.path).resolve()
        if directory.parent != self.root.resolve():
            errors.append("Evidence path is outside SACM_EVIDENCE_ROOT.")
        manifest_path = directory / "run-manifest.json"
        manifest: dict[str, Any] = {}
        if not manifest_path.is_file():
            errors.append("Evidence manifest is unavailable.")
        else:
            try:
                parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    manifest = parsed
                else:
                    errors.append("Evidence manifest is invalid.")
            except json.JSONDecodeError:
                errors.append("Evidence manifest is invalid JSON.")
            if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != pack.manifest_hash:
                errors.append("Evidence manifest checksum mismatch.")
        checksums_path = directory / "checksums.sha256"
        if not checksums_path.is_file():
            errors.append("Evidence checksums are unavailable.")
        else:
            for line in checksums_path.read_text(encoding="utf-8").splitlines():
                try:
                    expected, name = line.split("  ", 1)
                except ValueError:
                    errors.append("Evidence checksum file is malformed.")
                    continue
                target = directory / name
                if (
                    not target.is_file()
                    or hashlib.sha256(target.read_bytes()).hexdigest() != expected
                ):
                    errors.append(f"Checksum mismatch: {name}")
        signature_result = VerificationResultV1(
            status="UNSIGNED",
            chain_valid=True,
            errors=[],
        )
        signature_path = directory / "signature.sig"
        if signature_path.is_file():
            try:
                signed = json.loads(signature_path.read_text(encoding="utf-8"))
                if (
                    signed.get("algorithm") == "hmac-sha256"
                    and "statement" not in signed
                ):
                    key = load_evidence_hmac_key()
                    expected_signature = (
                        hmac.new(
                            key.encode(),
                            manifest_path.read_bytes(),
                            hashlib.sha256,
                        ).hexdigest()
                        if key
                        else None
                    )
                    if expected_signature and hmac.compare_digest(
                        expected_signature, str(signed.get("signature", ""))
                    ):
                        signature_result = VerificationResultV1(
                            status="VALID",
                            algorithm="hmac-sha256",
                            key_id="legacy-hmac",
                            chain_valid=True,
                        )
                    else:
                        errors.append("Legacy HMAC signature is invalid.")
                else:
                    if signed.get("statement") != manifest:
                        errors.append("Signed manifest content mismatch.")
                    for field, recorded in (
                        ("algorithm", pack.signature_algorithm),
                        ("key_id", pack.signature_key_id),
                        ("public_key_fingerprint", pack.public_key_fingerprint),
                        ("public_key", pack.public_key),
                        ("signature", pack.signature),
                    ):
                        if signed.get(field) != recorded:
                            errors.append(f"Recorded evidence {field} mismatch.")
                    signature_result = SupplyChainService.verify_signed_statement(
                        signed
                    )
                    errors.extend(signature_result.errors)
            except (json.JSONDecodeError, AttributeError):
                errors.append("Evidence signature is invalid JSON.")
        previous = (
            self.db.query(EvidencePack)
            .filter(
                EvidencePack.run_id == run_id,
                EvidencePack.created_at < pack.created_at,
            )
            .order_by(EvidencePack.created_at.desc())
            .first()
        )
        if pack.pack_hash is None:
            chain_valid = previous is None
        else:
            expected_previous = previous.pack_hash if previous else None
            chain_valid = pack.previous_pack_hash == expected_previous
            if not chain_valid:
                errors.append("Evidence chain predecessor mismatch.")
            expected_pack_hash = canonical_sha256(
                {
                    "manifest_hash": pack.manifest_hash,
                    "previous_pack_hash": pack.previous_pack_hash,
                    "signature": pack.signature,
                }
            )
            if pack.pack_hash != expected_pack_hash:
                chain_valid = False
                errors.append("Evidence chain hash mismatch.")
        status: Literal["VALID", "INVALID", "UNSIGNED"]
        if signature_result.status == "UNSIGNED" and not errors:
            status = "UNSIGNED"
        else:
            status = "VALID" if not errors else "INVALID"
        pack.verification_status = status
        self.db.commit()
        return VerificationResultV1(
            status=status,
            algorithm=pack.signature_algorithm or signature_result.algorithm,
            key_id=pack.signature_key_id or signature_result.key_id,
            public_key_fingerprint=(
                pack.public_key_fingerprint
                or signature_result.public_key_fingerprint
            ),
            chain_valid=chain_valid,
            errors=errors,
        )

    def verify_chain(
        self,
        run_id: str,
        actor_id: str | None = None,
        *,
        trusted_internal: bool = False,
    ) -> VerificationResultV1:
        self._authorize(
            run_id,
            actor_id,
            "evidence.read",
            trusted_internal=trusted_internal,
        )
        packs = (
            self.db.query(EvidencePack)
            .filter(EvidencePack.run_id == run_id)
            .order_by(EvidencePack.created_at, EvidencePack.id)
            .all()
        )
        errors: list[str] = []
        for pack in packs:
            result = self.verify(
                run_id,
                pack.id,
                actor_id,
                trusted_internal=trusted_internal,
            )
            errors.extend(f"{pack.id}: {error}" for error in result.errors)
        return VerificationResultV1(
            status="VALID" if packs and not errors else ("UNSIGNED" if not packs else "INVALID"),
            chain_valid=not errors,
            errors=errors,
        )

    def ingest_artifact(
        self,
        run_id: str,
        artifact_type: str,
        source_path: str,
        actor_id: str | None = None,
        *,
        trusted_internal: bool = False,
    ) -> Artifact:
        run = self._authorize(
            run_id, actor_id, "evidence.build", trusted_internal=trusted_internal
        )
        if not run.target_repo_path:
            raise ValueError("A run with a target repository is required.")
        context = ResourceAuthorizationService(self.db).run_context(run)
        root = Path(run.target_repo_path).resolve()
        source = Path(source_path).resolve()
        if not source.is_file() or (source != root and root not in source.parents):
            raise ValueError("Artifact path must be a file inside the target repository.")
        artifact = Artifact(
            task_id=run.task_id,
            organization_id=context.organization_id if context else None,
            project_id=context.project_id if context else None,
            tenant_attribution=(
                {"schema_version": "tenant-attribution/v1", "source": context.source}
                if context
                else None
            ),
            **self._storage_metadata(context, "artifacts"),
            artifact_type=artifact_type,
            path=str(source),
            content_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
            metadata_={"run_id": run.id},
        )
        self.db.add(artifact)
        self.db.commit()
        self.db.refresh(artifact)
        if context and actor_id:
            TenancyService(self.db).audit_sensitive(
                context.organization_id,
                context.project_id,
                actor_id,
                "evidence.artifact.ingest",
                "artifact",
                artifact.id,
                "Evidence artifact ingested.",
                {"artifact_type": artifact_type},
            )
        return artifact

    def _storage_metadata(
        self, context: TenantContext | None, category: str
    ) -> dict[str, str | None]:
        if context is None:
            return {}
        storage = ResidencyService(self.db).resolve(
            organization_id=context.organization_id,
            project_id=context.project_id,
            category=category,
        )
        return {
            "storage_region": storage["region"],
            "storage_classification": storage["classification"],
            "storage_class": storage["storage_class"],
        }

    def manifest(
        self,
        run_id: str,
        evidence_id: str,
        actor_id: str | None = None,
        *,
        trusted_internal: bool = False,
    ) -> dict[str, Any]:
        self._authorize(
            run_id,
            actor_id,
            "evidence.read",
            evidence_id=evidence_id,
            trusted_internal=trusted_internal,
        )
        pack = (
            self.db.query(EvidencePack)
            .filter(
                EvidencePack.id == evidence_id,
                EvidencePack.run_id == run_id,
            )
            .first()
        )
        if pack is None:
            raise ValueError(f"Evidence pack {evidence_id} not found.")
        directory = Path(pack.path).resolve()
        root = self.root.resolve()
        if directory.parent != root:
            raise ValueError("Evidence path must remain inside SACM_EVIDENCE_ROOT.")
        manifest_path = directory / "run-manifest.json"
        if not manifest_path.is_file():
            raise ValueError("Evidence manifest is unavailable.")
        if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != pack.manifest_hash:
            raise ValueError("Evidence manifest checksum does not match its record.")
        content = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            raise ValueError("Evidence manifest is invalid.")
        return content

    def _authorize(
        self,
        run_id: str,
        actor_id: str | None,
        permission: str,
        *,
        evidence_id: str | None = None,
        trusted_internal: bool,
    ):
        resources = ResourceAuthorizationService(self.db)
        if actor_id is not None:
            return resources.require_evidence(
                run_id, actor_id, permission, evidence_id
            )
        if resources._production() and not trusted_internal:
            raise PermissionError("Authenticated tenant context is required.")
        run = self.runs.get(run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found")
        return run

    @staticmethod
    def _write_json(path: Path, content: Any) -> None:
        path.write_text(
            json.dumps(EvidenceService._sanitize(content), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _agent_records(self, task_id: str) -> list[dict[str, Any]]:
        events = (
            self.db.query(ContextEvent)
            .filter(ContextEvent.task_id == task_id, ContextEvent.event_type == "agent_result")
            .order_by(ContextEvent.created_at)
            .all()
        )
        return [
            {
                "task": event.payload["agent_task_contract"],
                "result": event.payload["agent_result_contract"],
            }
            for event in events
            if isinstance(event.payload.get("agent_task_contract"), dict)
            and isinstance(event.payload.get("agent_result_contract"), dict)
        ]

    def _write_recorded_artifacts(
        self, directory: Path, agent_records: list[dict[str, Any]]
    ) -> None:
        artifacts = [
            artifact
            for record in agent_records
            for result in [record["result"]]
            for artifact in result.get("artifacts", [])
            if isinstance(artifact, dict)
        ]
        diffs = [
            str(artifact["metadata"]["content"])
            for artifact in artifacts
            if artifact.get("artifact_type") == "diff"
            and isinstance(artifact.get("metadata"), dict)
            and isinstance(artifact["metadata"].get("content"), str)
        ]
        if diffs:
            (directory / "patch.diff").write_text(
                self._redact_text("\n".join(diffs)), encoding="utf-8"
            )

        reviews = [
            {
                "summary": result.get("summary"),
                "decisions": result.get("decisions", []),
                "findings": result.get("findings", []),
                "confidence": result.get("confidence"),
            }
            for record in agent_records
            for result in [record["result"]]
            if record["task"].get("role") == "reviewer"
        ]
        if reviews:
            self._write_json(directory / "review-report.json", reviews)

        verification = [
            artifact.get("metadata", {})
            for artifact in artifacts
            if artifact.get("artifact_type") == "verification"
        ]
        if verification:
            self._write_json(directory / "verification-results.json", verification)

        for artifact in artifacts:
            artifact_type = artifact.get("artifact_type")
            if not isinstance(artifact_type, str):
                continue
            target_name = {
                "test_results_junit": "test-results.xml",
                "security_findings_sarif": "security-findings.sarif",
            }.get(artifact_type)
            if target_name:
                self._copy_recorded_file(directory, target_name, artifact.get("uri"))

    def _copy_recorded_file(
        self, directory: Path, target_name: str, uri: object
    ) -> None:
        if not isinstance(uri, str) or not uri.startswith("file://"):
            return
        source = Path(uri.removeprefix("file://")).resolve()
        run = self.runs.get(directory.name)
        if run is None:
            return
        repository = run.target_repo_path
        if not repository:
            return
        root = Path(repository).resolve()
        if root not in source.parents or not source.is_file():
            return
        (directory / target_name).write_bytes(self._safe_artifact_bytes(source))

    def _write_provenance(self, directory: Path, run: Any, manifest: dict[str, Any]) -> None:
        manifest_hash = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self._write_json(
            directory / "provenance.intoto.jsonl",
            {
                "_type": "https://in-toto.io/Statement/v1",
                "subject": [
                    {
                        "name": "run-manifest.json",
                        "digest": {"sha256": manifest_hash},
                    }
                ],
                "predicateType": "https://slsa.dev/provenance/v1",
                "predicate": {
                    "buildDefinition": {
                        "buildType": "https://sacm.dev/run/v1",
                        "externalParameters": {"task_id": run.task_id},
                        "internalParameters": {"workflow_version": run.workflow_version},
                        "resolvedDependencies": [
                            {
                                "uri": str(run.target_repo_path or ""),
                                "digest": (
                                    {"gitCommit": run.source_revision}
                                    if run.source_revision
                                    else {}
                                ),
                            }
                        ],
                    },
                    "runDetails": {"builder": {"id": "sacm-agent-runtime"}},
                },
            },
        )

    def _write_signature(self, directory: Path) -> dict[str, Any] | None:
        if not self._signing_metadata()["present"]:
            return None
        manifest = json.loads(
            (directory / "run-manifest.json").read_text(encoding="utf-8")
        )
        signed = SupplyChainService.sign_statement(manifest)
        signed["signed_file"] = "run-manifest.json"
        self._write_json(directory / "signature.sig", signed)
        return signed

    @staticmethod
    def _signing_metadata() -> dict[str, Any]:
        private_key_file = os.getenv("SACM_EVIDENCE_SIGNING_PRIVATE_KEY_FILE")
        hmac_present = bool(
            os.getenv("SACM_EVIDENCE_HMAC_KEY_FILE")
            or os.getenv("SACM_EVIDENCE_HMAC_KEY")
        )
        return {
            "present": bool(private_key_file or hmac_present),
            "algorithm": (
                "ed25519" if private_key_file else ("hmac-sha256" if hmac_present else None)
            ),
            "key_id": os.getenv("SACM_EVIDENCE_SIGNING_KEY_ID"),
        }

    def _write_external_artifacts(self, directory: Path, run: Any) -> None:
        target_names = {
            "test_results_junit": "test-results.xml",
            "security_findings_sarif": "security-findings.sarif",
            "sbom_spdx": "sbom.spdx.json",
            "provenance_intoto": "provenance.intoto.jsonl",
            "patch_diff": "patch.diff",
        }
        artifacts = (
            self.db.query(Artifact)
            .filter(
                Artifact.task_id == run.task_id,
                Artifact.artifact_type.in_(target_names),
            )
            .all()
        )
        for artifact in artifacts:
            if not artifact.path:
                continue
            source = Path(artifact.path).resolve()
            root = Path(run.target_repo_path).resolve() if run.target_repo_path else None
            if (
                root is None
                or not source.is_file()
                or (source != root and root not in source.parents)
            ):
                continue
            if hashlib.sha256(source.read_bytes()).hexdigest() != artifact.content_hash:
                continue
            (directory / target_names[artifact.artifact_type]).write_bytes(
                self._safe_artifact_bytes(source)
            )

    def _task_context(self, run: Any) -> dict[str, Any]:
        task = run.task
        contract = task.task_contract or {
            "schema_version": task.contract_version,
            "title": task.title,
            "description": task.description,
        }
        return {
            "id": task.id,
            "contract": contract,
            "source": {
                "connector_type": task.connector_type,
                "external_id": task.external_id,
                "external_url": task.external_url,
            },
            "readiness": {
                "score": task.readiness_score,
                "details": task.readiness_details,
            },
        }

    @staticmethod
    def _application_context(run: Any) -> dict[str, Any] | None:
        context = run.task.application_context
        if context is None:
            return None
        return {
            "id": context.id,
            "schema_version": context.schema_version,
            "status": context.status,
            "scanner_version": context.scanner_version,
            "graph_hash": context.graph_hash,
            "impact_analysis": context.impact_analysis,
            "risk_analysis": context.risk_analysis,
            "repositories": [
                {
                    "id": repository.id,
                    "position": repository.position,
                    "full_name": repository.full_name,
                    "requested_path": repository.requested_path,
                    "resolved_path": repository.resolved_path,
                    "base_revision": repository.base_revision,
                    "status": repository.status,
                    "file_count": repository.file_count,
                    "skipped_file_count": repository.skipped_file_count,
                }
                for repository in context.repositories
            ],
        }

    def _execution_plan(self, run: Any) -> dict[str, Any] | None:
        plan = (
            self.db.query(ExecutionPlan)
            .filter(ExecutionPlan.task_id == run.task_id)
            .order_by(ExecutionPlan.revision.desc())
            .first()
        )
        if plan is None:
            return None
        return {
            "id": plan.id,
            "schema_version": plan.schema_version,
            "revision": plan.revision,
            "planner_version": plan.planner_version,
            "source_hash": plan.source_hash,
            "status": plan.status,
            "policy_pack": plan.policy_pack,
            "steps": [
                {
                    "id": step.id,
                    "sequence": step.sequence,
                    "stable_key": step.stable_key,
                    "kind": step.kind,
                    "title": step.title,
                    "objective": step.objective,
                    "acceptance_criteria": step.acceptance_criteria,
                    "context_references": step.context_references,
                    "impacted_node_ids": step.impacted_node_ids,
                    "required_tools": step.required_tools,
                    "risk_tags": step.risk_tags,
                    "depends_on": step.depends_on,
                    "assigned_agent": {
                        "name": step.assigned_agent_name,
                        "role": step.assigned_agent_role,
                        "provider": step.agent_configuration.get("provider")
                        or step.agent_configuration.get("configuration", {}).get(
                            "provider"
                        ),
                        "model": step.agent_configuration.get("model")
                        or step.agent_configuration.get("configuration", {}).get(
                            "model"
                        ),
                        "framework": step.agent_configuration.get("framework")
                        or step.agent_configuration.get("runtime_kind")
                        or step.agent_configuration.get("configuration", {}).get(
                            "framework"
                        )
                        or step.agent_configuration.get("configuration", {}).get(
                            "adapter"
                        ),
                        "configuration": step.agent_configuration,
                    },
                }
                for step in plan.steps
            ],
        }

    def _policy_security_approvals(
        self, run: Any, execution_plan: dict[str, Any] | None
    ) -> dict[str, Any]:
        plan = (
            self.db.get(ExecutionPlan, execution_plan["id"])
            if execution_plan is not None
            else None
        )
        approvals = (
            self.db.query(Approval)
            .filter(Approval.run_id == run.id)
            .order_by(Approval.requested_at, Approval.id)
            .all()
        )
        return {
            "risk_decision": (
                plan.risk_decision.decision
                if plan is not None and plan.risk_decision is not None
                else None
            ),
            "policy_decision": (
                plan.policy_decision.decision
                if plan is not None and plan.policy_decision is not None
                else None
            ),
            "security_review": (
                {
                    "id": plan.security_review.id,
                    "required": plan.security_review.required,
                    "status": plan.security_review.status,
                    "reviewer": plan.security_review.reviewer_configuration,
                    "findings": plan.security_review.findings,
                    "reviewed_at": plan.security_review.reviewed_at,
                    "reviewed_by": plan.security_review.reviewed_by,
                }
                if plan is not None and plan.security_review is not None
                else None
            ),
            "plan_approval_gates": (
                [
                    {
                        "id": gate.id,
                        "gate_type": gate.gate_type,
                        "action": gate.action,
                        "reason": gate.reason,
                        "status": gate.status,
                        "step_ids": gate.step_ids,
                        "approval_id": gate.approval_id,
                    }
                    for gate in plan.approval_gates
                ]
                if plan is not None
                else []
            ),
            "run_approvals": [
                {
                    "id": approval.id,
                    "action": approval.action,
                    "resource": approval.resource,
                    "status": approval.status,
                    "requested_at": approval.requested_at,
                    "decided_at": approval.decided_at,
                    "decided_by": approval.decided_by,
                    "decision_reason": approval.decision_reason,
                }
                for approval in approvals
            ],
        }

    def _delivery_evidence(self, run: Any) -> dict[str, Any]:
        events = (
            self.db.query(ContextEvent)
            .filter(ContextEvent.task_id == run.task_id)
            .order_by(ContextEvent.created_at, ContextEvent.id)
            .all()
        )
        commands: list[dict[str, Any]] = []
        changed_files: set[str] = set()
        commits: set[str] = set()
        diffs: set[str] = set()
        verification: list[dict[str, Any]] = []
        for event in events:
            payload = event.payload
            command = payload.get("command")
            if isinstance(command, str):
                record = {
                    "event_id": event.id,
                    "command": command,
                    "returncode": payload.get("returncode"),
                    "passed": payload.get("passed"),
                }
                commands.append(record)
                if "verification" in event.event_type:
                    verification.append(record)
            files = payload.get("changed_files", [])
            if isinstance(files, list):
                changed_files.update(str(item) for item in files if item)
            for key in ("commit", "commit_sha", "target_revision"):
                value = payload.get(key)
                if isinstance(value, str):
                    commits.add(value)
            for key in ("diff_sha256",):
                value = payload.get(key)
                if isinstance(value, str):
                    diffs.add(value)
            result = payload.get("agent_result_contract")
            if isinstance(result, dict):
                for item in result.get("evidence", []):
                    if isinstance(item, dict):
                        verification.append(
                            {"event_id": event.id, "evidence": item}
                        )
        return {
            "commands": commands,
            "tests_and_verification": verification,
            "changed_files": sorted(changed_files),
            "commit_refs": sorted(commits),
            "diff_hashes": sorted(diffs),
        }

    def _artifact_inventory(self, run: Any) -> list[dict[str, Any]]:
        artifacts = (
            self.db.query(Artifact)
            .filter(Artifact.task_id == run.task_id)
            .order_by(Artifact.created_at, Artifact.id)
            .all()
        )
        return [
            {
                "id": artifact.id,
                "artifact_type": artifact.artifact_type,
                "path": artifact.path,
                "sha256": artifact.content_hash,
                "metadata": artifact.metadata_,
            }
            for artifact in artifacts
        ]

    def _supply_chain_records(self, run_id: str) -> list[dict[str, Any]]:
        records = (
            self.db.query(SupplyChainRecord)
            .filter(SupplyChainRecord.run_id == run_id)
            .order_by(SupplyChainRecord.created_at, SupplyChainRecord.id)
            .all()
        )
        return [
            {
                "id": record.id,
                "schema_version": record.schema_version,
                "record_type": record.record_type,
                "format": record.format,
                "subject": {
                    "name": record.subject_name,
                    "digest": {"sha256": record.subject_digest},
                },
                "artifact_sha256": record.artifact_sha256,
                "status": record.status,
                "coverage": record.coverage,
                "artifact_id": record.artifact_id,
                "image_id": record.image_id,
                "release_id": record.release_id,
            }
            for record in records
        ]

    @staticmethod
    def _sanitize(value: Any, *, sensitive: bool = False) -> Any:
        if sensitive:
            return "[REDACTED]"
        if isinstance(value, dict):
            return {
                str(key): EvidenceService._sanitize(
                    item, sensitive=bool(_SECRET_KEY.search(str(key)))
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [EvidenceService._sanitize(item) for item in value]
        if isinstance(value, tuple):
            return [EvidenceService._sanitize(item) for item in value]
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, str):
            return EvidenceService._redact_text(value)
        return value

    @staticmethod
    def _redact_text(value: str) -> str:
        redacted = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)
        for key, secret in os.environ.items():
            if _SECRET_KEY.search(key) and len(secret) >= 4:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redacted

    @classmethod
    def _safe_artifact_bytes(cls, source: Path) -> bytes:
        content = source.read_bytes()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            for key, secret in os.environ.items():
                if (
                    _SECRET_KEY.search(key)
                    and len(secret) >= 4
                    and secret.encode() in content
                ):
                    raise ValueError(
                        f"Artifact {source.name} contains secret material."
                    ) from None
            return content
        return cls._redact_text(text).encode()

    @staticmethod
    def _checksums(directory: Path) -> str:
        return "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in sorted(directory.iterdir())
            if path.is_file() and path.name != "checksums.sha256"
        )

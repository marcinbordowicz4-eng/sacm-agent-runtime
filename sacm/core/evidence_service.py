import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from sacm.core.cost_service import CostService
from sacm.core.run_service import RunService
from sacm.infrastructure.db.models import Artifact, ContextEvent, EvidencePack


class EvidenceService:
    """Builds a hash-checked pack from recorded artifacts without fabricating evidence."""

    def __init__(self, db: Session, root: str | None = None) -> None:
        self.db = db
        self.runs = RunService(db)
        self.root = Path(
            root if root is not None else os.getenv("SACM_EVIDENCE_ROOT", ".sacm/evidence")
        )

    def build(self, run_id: str) -> EvidencePack:
        run = self.runs.get(run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found")
        directory = (self.root / run_id).resolve()
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
        events = self.runs.events(run_id)
        steps = self.runs.list_steps(run_id)
        manifest = {
            "schema_version": "run-manifest/v2",
            "run_id": run.id,
            "task_id": run.task_id,
            "status": run.status,
            "workflow_version": run.workflow_version,
            "source_revision": run.source_revision,
            "event_chain_hash": events[-1].event_hash if events else None,
        }
        self._write_json(
            directory / "run-manifest.json",
            manifest,
        )
        self._write_json(
            directory / "request.json",
            {"title": run.task.title, "description": run.task.description},
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
                            "payload": event.payload,
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
            CostService(self.db).summarize_task(run.task_id),
        )
        agent_records = self._agent_records(run.task_id)
        self._write_recorded_artifacts(directory, agent_records)
        self._write_provenance(directory, run, manifest)
        self._write_external_artifacts(directory, run)
        self._write_signature(directory)
        checksums = self._checksums(directory)
        (directory / "checksums.sha256").write_text(checksums, encoding="utf-8")
        manifest_hash = hashlib.sha256(
            (directory / "run-manifest.json").read_bytes()
        ).hexdigest()
        pack = EvidencePack(
            run_id=run.id,
            path=str(directory),
            manifest_hash=manifest_hash,
        )
        self.db.add(pack)
        self.db.commit()
        self.db.refresh(pack)
        return pack

    def ingest_artifact(
        self, run_id: str, artifact_type: str, source_path: str
    ) -> Artifact:
        run = self.runs.get(run_id)
        if not run or not run.target_repo_path:
            raise ValueError("A run with a target repository is required.")
        root = Path(run.target_repo_path).resolve()
        source = Path(source_path).resolve()
        if not source.is_file() or (source != root and root not in source.parents):
            raise ValueError("Artifact path must be a file inside the target repository.")
        artifact = Artifact(
            task_id=run.task_id,
            artifact_type=artifact_type,
            path=str(source),
            content_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
            metadata_={"run_id": run.id},
        )
        self.db.add(artifact)
        self.db.commit()
        self.db.refresh(artifact)
        return artifact

    @staticmethod
    def _write_json(path: Path, content: Any) -> None:
        path.write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")

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
            (directory / "patch.diff").write_text("\n".join(diffs), encoding="utf-8")

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
        (directory / target_name).write_bytes(source.read_bytes())

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

    def _write_signature(self, directory: Path) -> None:
        key = os.getenv("SACM_EVIDENCE_HMAC_KEY")
        if not key:
            return
        manifest = (directory / "run-manifest.json").read_bytes()
        signature = hmac.new(key.encode(), manifest, hashlib.sha256).hexdigest()
        self._write_json(
            directory / "signature.sig",
            {
                "algorithm": "hmac-sha256",
                "signed_file": "run-manifest.json",
                "signature": signature,
            },
        )

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
                source.read_bytes()
            )

    @staticmethod
    def _checksums(directory: Path) -> str:
        return "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in sorted(directory.iterdir())
            if path.is_file() and path.name != "checksums.sha256"
        )

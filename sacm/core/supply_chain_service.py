import base64
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.orm import Session

from sacm.core.governance_service import ResidencyService
from sacm.infrastructure.db.models import (
    Artifact,
    EvidencePack,
    Run,
    SupplyChainAttestation,
    SupplyChainImage,
    SupplyChainRecord,
    SupplyChainRelease,
)
from sacm.schemas.supply_chain import (
    AttestationCreateV1,
    ImageCreateV1,
    ProvenanceCreateV1,
    ReleaseCreateV1,
    SupplyChainCompletenessV1,
    SupplyChainRecordCreateV1,
    VerificationResultV1,
)

MANDATORY_SUPPLY_CHAIN_TYPES = (
    "sbom",
    "provenance",
    "dependency_scan",
    "secret_scan",
    "iac_scan",
    "container_scan",
)
_SUCCESS_STATUSES = {"PASSED", "COMPLETE", "VERIFIED"}
_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:api_?key|authorization|credential|password|private_?key|secret|token)(?:$|_)",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?im)\b(api[_-]?key|authorization|credential|password|private[_-]?key|secret|token)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def normalize_sha256(value: str) -> str:
    digest = value.removeprefix("sha256:").lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("A valid SHA-256 digest is required.")
    return digest


class SupplyChainService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_image(self, run_id: str, payload: ImageCreateV1) -> SupplyChainImage:
        self._require_run(run_id)
        image = SupplyChainImage(
            schema_version=payload.schema_version,
            run_id=run_id,
            name=payload.name,
            digest=normalize_sha256(payload.digest),
            repository=payload.repository,
            tag=payload.tag,
            metadata_=payload.metadata,
        )
        self.db.add(image)
        self.db.commit()
        self.db.refresh(image)
        return image

    def create_release(
        self, run_id: str, payload: ReleaseCreateV1
    ) -> SupplyChainRelease:
        self._require_run(run_id)
        release = SupplyChainRelease(
            schema_version=payload.schema_version,
            run_id=run_id,
            name=payload.name,
            version=payload.version,
            digest=normalize_sha256(payload.digest),
            uri=payload.uri,
            metadata_=payload.metadata,
        )
        self.db.add(release)
        self.db.commit()
        self.db.refresh(release)
        return release

    def ingest(
        self, run_id: str, payload: SupplyChainRecordCreateV1
    ) -> SupplyChainRecord:
        run = self._require_run(run_id)
        content_hash = canonical_sha256(payload.content)
        if normalize_sha256(payload.artifact_sha256) != content_hash:
            raise ValueError(
                "artifact_sha256 must match the canonical JSON artifact content."
            )
        subject_digest = normalize_sha256(payload.subject.digest)
        self._validate_record(payload, subject_digest)
        self._validate_links(run, payload)
        artifact = self._artifact(run, payload, content_hash)
        sanitized_content = _sanitize(payload.content)
        sanitized_metadata = _sanitize(payload.metadata)
        if not isinstance(sanitized_content, dict) or not isinstance(
            sanitized_metadata, dict
        ):
            raise ValueError("Supply-chain content and metadata must be objects.")
        sanitized_metadata["stored_content_sha256"] = canonical_sha256(
            sanitized_content
        )
        record = SupplyChainRecord(
            schema_version=payload.schema_version,
            run_id=run.id,
            evidence_pack_id=payload.evidence_pack_id,
            artifact_id=artifact.id,
            image_id=payload.image_id,
            release_id=payload.release_id,
            record_type=payload.record_type,
            format=payload.format,
            subject_name=payload.subject.name,
            subject_digest=subject_digest,
            artifact_sha256=content_hash,
            status=payload.status,
            coverage=payload.coverage,
            content=sanitized_content,
            metadata_=sanitized_metadata,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        self.refresh_completeness(run.id)
        return record

    def create_provenance(
        self, run_id: str, payload: ProvenanceCreateV1
    ) -> SupplyChainRecord:
        run = self._require_run(run_id)
        subject_digest = normalize_sha256(payload.subject.digest)
        content = {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {
                    "name": payload.subject.name,
                    "digest": {"sha256": subject_digest},
                }
            ],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {
                "buildDefinition": {
                    "buildType": "https://sacm.dev/build/v1",
                    "externalParameters": {
                        "source": {
                            "repository": payload.source_repository,
                            "revision": payload.source_revision,
                        },
                        "commands": payload.build_commands,
                    },
                    "internalParameters": {
                        "run_id": run.id,
                        "task_id": run.task_id,
                        "workflow_version": run.workflow_version,
                        "agent": payload.agent,
                        "model": payload.model,
                        "framework": payload.framework,
                        "policy_decisions": payload.policy_decisions,
                        "security_decisions": payload.security_decisions,
                        "snapshot_ids": payload.snapshot_ids,
                        "replay_id": payload.replay_id,
                    },
                    "resolvedDependencies": [
                        {
                            "uri": material.name,
                            "digest": {
                                "sha256": normalize_sha256(material.digest)
                            },
                        }
                        for material in payload.materials
                    ],
                },
                "runDetails": {
                    "builder": {"id": payload.builder_id},
                    "metadata": {
                        "executor": payload.executor_id,
                        "environment": payload.environment,
                        "image_digest": payload.image_digest,
                    },
                    "byproducts": [
                        {
                            "name": product.name,
                            "digest": {
                                "sha256": normalize_sha256(product.digest)
                            },
                        }
                        for product in payload.products
                    ],
                },
            },
        }
        record_payload = SupplyChainRecordCreateV1(
            record_type="provenance",
            format="in-toto-slsa-v1",
            subject=payload.subject,
            artifact_sha256=canonical_sha256(content),
            status="VERIFIED",
            coverage={
                "source": True,
                "builder": True,
                "commands": len(payload.build_commands),
                "materials": len(payload.materials),
                "products": len(payload.products),
                "environment": bool(payload.environment or payload.image_digest),
            },
            content=content,
            metadata={"schema_version": payload.schema_version},
        )
        return self.ingest(run_id, record_payload)

    def attest(
        self, run_id: str, payload: AttestationCreateV1
    ) -> SupplyChainAttestation:
        run = self._require_run(run_id)
        self._validate_attestation_links(run, payload)
        digest = normalize_sha256(payload.subject.digest)
        statement = {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {"name": payload.subject.name, "digest": {"sha256": digest}}
            ],
            "predicateType": payload.predicate_type,
            "predicate": payload.predicate,
        }
        previous = (
            self.db.query(SupplyChainAttestation)
            .filter(SupplyChainAttestation.run_id == run_id)
            .order_by(SupplyChainAttestation.created_at.desc())
            .first()
        )
        signed = self.sign_statement(statement, key_id=payload.key_id)
        attestation_hash = canonical_sha256(
            {
                "statement_hash": canonical_sha256(statement),
                "previous_attestation_hash": (
                    previous.attestation_hash if previous else None
                ),
                "signature": signed["signature"],
            }
        )
        attestation = SupplyChainAttestation(
            schema_version=payload.schema_version,
            run_id=run_id,
            record_id=payload.record_id,
            artifact_id=payload.artifact_id,
            image_id=payload.image_id,
            release_id=payload.release_id,
            subject_name=payload.subject.name,
            subject_digest=digest,
            predicate_type=payload.predicate_type,
            statement=statement,
            statement_hash=canonical_sha256(statement),
            previous_attestation_hash=previous.attestation_hash if previous else None,
            attestation_hash=attestation_hash,
            signature_algorithm=signed["algorithm"],
            signature_key_id=signed.get("key_id"),
            public_key_fingerprint=signed.get("public_key_fingerprint"),
            public_key=signed.get("public_key"),
            signature=signed["signature"],
            verification_status="UNVERIFIED",
        )
        self.db.add(attestation)
        self.db.commit()
        self.db.refresh(attestation)
        self.verify_attestation(attestation)
        return attestation

    def verify_attestation(
        self, attestation: SupplyChainAttestation
    ) -> VerificationResultV1:
        errors: list[str] = []
        if canonical_sha256(attestation.statement) != attestation.statement_hash:
            errors.append("Statement hash mismatch.")
        subject = attestation.statement.get("subject", [])
        expected_subject = {
            "name": attestation.subject_name,
            "digest": {"sha256": attestation.subject_digest},
        }
        if not isinstance(subject, list) or expected_subject not in subject:
            errors.append("Attestation subject identity mismatch.")
        signed = {
            "statement": attestation.statement,
            "algorithm": attestation.signature_algorithm,
            "key_id": attestation.signature_key_id,
            "public_key_fingerprint": attestation.public_key_fingerprint,
            "public_key": attestation.public_key,
            "signature": attestation.signature,
        }
        signature_result = self.verify_signed_statement(signed)
        errors.extend(signature_result.errors)
        previous = (
            self.db.query(SupplyChainAttestation)
            .filter(
                SupplyChainAttestation.run_id == attestation.run_id,
                SupplyChainAttestation.created_at < attestation.created_at,
            )
            .order_by(SupplyChainAttestation.created_at.desc())
            .first()
        )
        expected_previous = previous.attestation_hash if previous else None
        chain_valid = attestation.previous_attestation_hash == expected_previous
        if not chain_valid:
            errors.append("Attestation chain predecessor mismatch.")
        expected_hash = canonical_sha256(
            {
                "statement_hash": attestation.statement_hash,
                "previous_attestation_hash": attestation.previous_attestation_hash,
                "signature": attestation.signature,
            }
        )
        if expected_hash != attestation.attestation_hash:
            chain_valid = False
            errors.append("Attestation chain hash mismatch.")
        status: Literal["VALID", "INVALID"] = "VALID" if not errors else "INVALID"
        attestation.verification_status = status
        self.db.commit()
        return VerificationResultV1(
            status=status,
            algorithm=attestation.signature_algorithm,
            key_id=attestation.signature_key_id,
            public_key_fingerprint=attestation.public_key_fingerprint,
            chain_valid=chain_valid,
            errors=errors,
        )

    def verify_chain(self, run_id: str) -> VerificationResultV1:
        records = (
            self.db.query(SupplyChainAttestation)
            .filter(SupplyChainAttestation.run_id == run_id)
            .order_by(SupplyChainAttestation.created_at, SupplyChainAttestation.id)
            .all()
        )
        errors: list[str] = []
        previous_hash: str | None = None
        for record in records:
            if record.previous_attestation_hash != previous_hash:
                errors.append(f"{record.id}: predecessor mismatch")
            result = self.verify_attestation(record)
            errors.extend(f"{record.id}: {error}" for error in result.errors)
            previous_hash = record.attestation_hash
        return VerificationResultV1(
            status="VALID" if records and not errors else ("UNSIGNED" if not records else "INVALID"),
            chain_valid=not errors,
            errors=errors,
        )

    def completeness(self, run_id: str) -> SupplyChainCompletenessV1:
        successful = {
            record.record_type
            for record in self.db.query(SupplyChainRecord)
            .filter(SupplyChainRecord.run_id == run_id)
            .all()
            if record.status in _SUCCESS_STATUSES and bool(record.coverage)
        }
        missing = sorted(set(MANDATORY_SUPPLY_CHAIN_TYPES) - successful)
        return SupplyChainCompletenessV1(
            status="INCOMPLETE" if missing else "COMPLETE",
            mandatory_types=list(MANDATORY_SUPPLY_CHAIN_TYPES),
            present_types=sorted(successful),
            missing_types=missing,
        )

    def refresh_completeness(self, run_id: str) -> SupplyChainCompletenessV1:
        result = self.completeness(run_id)
        run = self._require_run(run_id)
        run.supply_chain_status = result.status
        run.missing_supply_chain_evidence = result.missing_types
        self.db.commit()
        return result

    @staticmethod
    def sign_statement(
        statement: dict[str, Any],
        *,
        private_key_file: str | None = None,
        key_id: str | None = None,
        hmac_key: str | None = None,
    ) -> dict[str, Any]:
        key_file = private_key_file or os.getenv(
            "SACM_EVIDENCE_SIGNING_PRIVATE_KEY_FILE"
        )
        if key_file:
            serialization, ed25519, _ = _crypto()
            key = serialization.load_pem_private_key(
                Path(key_file).read_bytes(), password=None
            )
            if not isinstance(key, ed25519.Ed25519PrivateKey):
                raise ValueError("Evidence signing key must be an Ed25519 private key.")
            public = key.public_key()
            public_pem = public.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode()
            fingerprint = _public_key_fingerprint(public_pem)
            return {
                "schema_version": "signed-statement/v1",
                "statement": statement,
                "algorithm": "ed25519",
                "key_id": key_id
                or os.getenv("SACM_EVIDENCE_SIGNING_KEY_ID")
                or fingerprint[:16],
                "public_key_fingerprint": fingerprint,
                "public_key": public_pem,
                "signature": base64.b64encode(
                    key.sign(canonical_json(statement))
                ).decode(),
            }
        key = hmac_key or load_evidence_hmac_key()
        if key:
            return {
                "schema_version": "signed-statement/v1",
                "statement": statement,
                "algorithm": "hmac-sha256",
                "key_id": key_id or "legacy-hmac",
                "public_key_fingerprint": None,
                "public_key": None,
                "signature": hmac.new(
                    key.encode(), canonical_json(statement), hashlib.sha256
                ).hexdigest(),
            }
        raise RuntimeError(
            "Configure SACM_EVIDENCE_SIGNING_PRIVATE_KEY_FILE or an HMAC key."
        )

    @staticmethod
    def verify_signed_statement(
        signed: dict[str, Any], *, hmac_key: str | None = None
    ) -> VerificationResultV1:
        errors: list[str] = []
        statement = signed.get("statement")
        algorithm = signed.get("algorithm")
        signature = signed.get("signature")
        if not isinstance(statement, dict) or not isinstance(signature, str):
            return VerificationResultV1(
                status="UNSIGNED",
                algorithm=algorithm if isinstance(algorithm, str) else None,
                chain_valid=False,
                errors=["Signed statement is incomplete."],
            )
        try:
            if algorithm == "ed25519":
                public_key = signed.get("public_key")
                if not isinstance(public_key, str):
                    raise ValueError("Ed25519 public key is missing.")
                fingerprint = _public_key_fingerprint(public_key)
                if fingerprint != signed.get("public_key_fingerprint"):
                    raise ValueError("Public key fingerprint mismatch.")
                serialization, ed25519, invalid_signature = _crypto()
                key = serialization.load_pem_public_key(public_key.encode())
                if not isinstance(key, ed25519.Ed25519PublicKey):
                    raise ValueError("Public key is not Ed25519.")
                try:
                    key.verify(
                        base64.b64decode(signature, validate=True),
                        canonical_json(statement),
                    )
                except (ValueError, invalid_signature) as exc:
                    raise ValueError("Ed25519 signature is invalid.") from exc
            elif algorithm == "hmac-sha256":
                key = hmac_key or load_evidence_hmac_key()
                if not key:
                    raise ValueError("HMAC verification key is unavailable.")
                expected = hmac.new(
                    key.encode(), canonical_json(statement), hashlib.sha256
                ).hexdigest()
                if not hmac.compare_digest(expected, signature):
                    raise ValueError("HMAC signature is invalid.")
            else:
                raise ValueError("Unsupported signature algorithm.")
        except (ValueError, TypeError) as exc:
            errors.append(str(exc))
        return VerificationResultV1(
            status="VALID" if not errors else "INVALID",
            algorithm=algorithm,
            key_id=signed.get("key_id"),
            public_key_fingerprint=signed.get("public_key_fingerprint"),
            chain_valid=not errors,
            errors=errors,
        )

    def _artifact(
        self, run: Run, payload: SupplyChainRecordCreateV1, content_hash: str
    ) -> Artifact:
        if payload.artifact_id:
            artifact = self.db.get(Artifact, payload.artifact_id)
            if artifact is None or artifact.task_id != run.task_id:
                raise ValueError("Artifact does not belong to the run task.")
            if normalize_sha256(artifact.content_hash or "") != content_hash:
                raise ValueError("Artifact content hash does not match.")
            return artifact
        artifact = Artifact(
            task_id=run.task_id,
            organization_id=run.organization_id,
            project_id=run.project_id,
            tenant_attribution=run.tenant_attribution,
            **self._artifact_storage(run),
            artifact_type=payload.record_type,
            content_hash=content_hash,
            metadata_={
                "schema_version": payload.schema_version,
                "format": payload.format,
                "run_id": run.id,
                "subject": payload.subject.model_dump(),
            },
        )
        self.db.add(artifact)
        self.db.flush()
        return artifact

    def _artifact_storage(self, run: Run) -> dict[str, str | None]:
        if not run.organization_id:
            return {}
        storage = ResidencyService(self.db).resolve(
            organization_id=run.organization_id,
            project_id=run.project_id,
            category="artifacts",
        )
        return {
            "storage_region": storage["region"],
            "storage_classification": storage["classification"],
            "storage_class": storage["storage_class"],
        }

    def _validate_record(
        self, payload: SupplyChainRecordCreateV1, subject_digest: str
    ) -> None:
        if not payload.coverage:
            raise ValueError("Supply-chain coverage metadata is required.")
        if payload.record_type == "sbom":
            self._validate_sbom(payload, subject_digest)
        elif payload.record_type == "provenance":
            subjects = payload.content.get("subject")
            expected = {
                "name": payload.subject.name,
                "digest": {"sha256": subject_digest},
            }
            if not isinstance(subjects, list) or expected not in subjects:
                raise ValueError("Provenance subject identity does not match.")
        elif not isinstance(payload.metadata.get("tool"), str):
            raise ValueError("Scan metadata must identify the scanner tool.")

    @staticmethod
    def _validate_sbom(
        payload: SupplyChainRecordCreateV1, subject_digest: str
    ) -> None:
        content = payload.content
        format_name = payload.format.lower()
        if "spdx" in format_name:
            if not str(content.get("spdxVersion", "")).startswith("SPDX-"):
                raise ValueError("SPDX SBOM requires spdxVersion.")
            if content.get("SPDXID") != "SPDXRef-DOCUMENT":
                raise ValueError("SPDX SBOM requires SPDXRef-DOCUMENT.")
            if not isinstance(content.get("packages"), list):
                raise ValueError("SPDX SBOM requires a packages array.")
            if content.get("name") != payload.subject.name:
                raise ValueError("SPDX document name must match the subject.")
        elif "cyclonedx" in format_name:
            if content.get("bomFormat") != "CycloneDX":
                raise ValueError("CycloneDX SBOM requires bomFormat=CycloneDX.")
            if not content.get("specVersion") or not isinstance(
                content.get("components"), list
            ):
                raise ValueError(
                    "CycloneDX SBOM requires specVersion and components."
                )
            component = content.get("metadata", {}).get("component", {})
            if component.get("name") != payload.subject.name:
                raise ValueError("CycloneDX component name must match the subject.")
            hashes = component.get("hashes", [])
            sha256_values = {
                str(item.get("content", "")).lower()
                for item in hashes
                if str(item.get("alg", "")).upper() in {"SHA-256", "SHA256"}
            }
            if sha256_values and subject_digest not in sha256_values:
                raise ValueError("CycloneDX subject digest does not match.")
        else:
            raise ValueError("SBOM format must be SPDX JSON or CycloneDX JSON.")

    def _validate_links(
        self, run: Run, payload: SupplyChainRecordCreateV1
    ) -> None:
        if payload.evidence_pack_id:
            pack = self.db.get(EvidencePack, payload.evidence_pack_id)
            if pack is None or pack.run_id != run.id:
                raise ValueError("Evidence pack does not belong to the run.")
        self._validate_subject_link(
            run, payload.subject.name, payload.subject.digest, payload.image_id, payload.release_id
        )

    def _validate_attestation_links(
        self, run: Run, payload: AttestationCreateV1
    ) -> None:
        if payload.record_id:
            record = self.db.get(SupplyChainRecord, payload.record_id)
            if record is None or record.run_id != run.id:
                raise ValueError("Supply-chain record does not belong to the run.")
        if payload.artifact_id:
            artifact = self.db.get(Artifact, payload.artifact_id)
            if artifact is None or artifact.task_id != run.task_id:
                raise ValueError("Artifact does not belong to the run.")
        self._validate_subject_link(
            run, payload.subject.name, payload.subject.digest, payload.image_id, payload.release_id
        )

    def _validate_subject_link(
        self,
        run: Run,
        name: str,
        digest: str,
        image_id: str | None,
        release_id: str | None,
    ) -> None:
        normalized = normalize_sha256(digest)
        if image_id:
            image = self.db.get(SupplyChainImage, image_id)
            if (
                image is None
                or image.run_id != run.id
                or image.name != name
                or image.digest != normalized
            ):
                raise ValueError("Image subject identity does not match.")
        if release_id:
            release = self.db.get(SupplyChainRelease, release_id)
            if (
                release is None
                or release.run_id != run.id
                or release.name != name
                or release.digest != normalized
            ):
                raise ValueError("Release subject identity does not match.")

    def _require_run(self, run_id: str) -> Run:
        run = self.db.get(Run, run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found.")
        return run


def load_evidence_hmac_key() -> str | None:
    file_name = os.getenv("SACM_EVIDENCE_HMAC_KEY_FILE")
    if file_name:
        return Path(file_name).read_text(encoding="utf-8").strip()
    return os.getenv("SACM_EVIDENCE_HMAC_KEY")


def _sanitize(value: Any, *, sensitive: bool = False) -> Any:
    if sensitive:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(key): _sanitize(
                item, sensitive=bool(_SENSITIVE_KEY.search(str(key)))
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        redacted = _SENSITIVE_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)
        for key, secret in os.environ.items():
            if _SENSITIVE_KEY.search(key) and len(secret) >= 4:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    return value


def _public_key_fingerprint(public_key_pem: str) -> str:
    serialization, ed25519, _ = _crypto()
    key = serialization.load_pem_public_key(public_key_pem.encode())
    if not isinstance(key, ed25519.Ed25519PublicKey):
        raise ValueError("Public key is not Ed25519.")
    der = key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


def _crypto() -> tuple[Any, Any, type[Exception]]:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:
        raise RuntimeError(
            "Ed25519 evidence signing requires: pip install -e '.[auth]'"
        ) from exc
    return serialization, ed25519, InvalidSignature

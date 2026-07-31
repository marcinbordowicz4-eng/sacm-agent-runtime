import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from sacm.core.evidence_service import EvidenceService
from sacm.core.run_service import RunService
from sacm.core.supply_chain_service import (
    SupplyChainService,
    canonical_sha256,
)
from sacm.infrastructure.db.models import SupplyChainAttestation
from sacm.schemas.run import RunCreate
from sacm.schemas.supply_chain import (
    AttestationCreateV1,
    ImageCreateV1,
    ProvenanceCreateV1,
    SupplyChainRecordCreateV1,
    SupplyChainSubjectV1,
)


def _run(db):
    return RunService(db).create(
        RunCreate(title="Supply chain", description="Verify build evidence.")
    )


def _subject(name: str = "runtime") -> SupplyChainSubjectV1:
    return SupplyChainSubjectV1(name=name, digest="a" * 64)


def test_spdx_and_cyclonedx_basics_and_hashes_are_validated(db):
    run = _run(db)
    service = SupplyChainService(db)
    spdx = {
        "spdxVersion": "SPDX-2.3",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "runtime",
        "packages": [],
    }
    record = service.ingest(
        run.id,
        SupplyChainRecordCreateV1(
            record_type="sbom",
            format="spdx-json",
            subject=_subject(),
            artifact_sha256=canonical_sha256(spdx),
            status="COMPLETE",
            coverage={"packages": 0, "files_analyzed": True},
            content=spdx,
        ),
    )

    assert record.artifact_id
    assert record.artifact_sha256 == canonical_sha256(spdx)

    cyclonedx = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "metadata": {"component": {"name": "other"}},
        "components": [],
    }
    with pytest.raises(ValueError, match="component name"):
        service.ingest(
            run.id,
            SupplyChainRecordCreateV1(
                record_type="sbom",
                format="cyclonedx-json",
                subject=_subject(),
                artifact_sha256=canonical_sha256(cyclonedx),
                status="COMPLETE",
                coverage={"components": 0},
                content=cyclonedx,
            ),
        )

    with pytest.raises(ValueError, match="artifact_sha256"):
        service.ingest(
            run.id,
            SupplyChainRecordCreateV1(
                record_type="sbom",
                format="spdx-json",
                subject=_subject(),
                artifact_sha256="b" * 64,
                status="COMPLETE",
                coverage={"packages": 0},
                content=spdx,
            ),
        )


def test_provenance_is_canonical_and_contains_replay_security_and_environment(db):
    run = _run(db)
    payload = ProvenanceCreateV1(
        subject=_subject(),
        source_repository="https://example.test/runtime",
        source_revision="abc123",
        builder_id="builder/1",
        executor_id="executor/1",
        build_commands=["python -m build"],
        materials=[SupplyChainSubjectV1(name="source", digest="b" * 64)],
        products=[_subject()],
        agent="copilot",
        model="model-1",
        framework="sacm",
        policy_decisions=[{"decision": "allow"}],
        security_decisions=[{"decision": "reviewed"}],
        snapshot_ids=["snapshot-1"],
        replay_id="replay-1",
        environment={"os": "linux"},
        image_digest="sha256:" + "c" * 64,
    )

    record = SupplyChainService(db).create_provenance(run.id, payload)

    assert record.content["predicate"]["buildDefinition"]["internalParameters"][
        "replay_id"
    ] == "replay-1"
    assert record.content["predicate"]["runDetails"]["metadata"]["image_digest"]
    assert canonical_sha256(record.content) == record.artifact_sha256


def test_ed25519_attestation_tamper_is_explicitly_invalid_and_key_stays_out_of_db(
    db, tmp_path, monkeypatch
):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_file = tmp_path / "evidence-key.pem"
    key_file.write_bytes(private_pem)
    monkeypatch.setenv("SACM_EVIDENCE_SIGNING_PRIVATE_KEY_FILE", str(key_file))
    run = _run(db)
    service = SupplyChainService(db)
    image = service.create_image(
        run.id,
        ImageCreateV1(name="runtime", digest="a" * 64),
    )
    attestation = service.attest(
        run.id,
        AttestationCreateV1(
            subject=_subject(),
            predicate_type="https://sacm.dev/release/v1",
            predicate={"approved": True},
            image_id=image.id,
            key_id="evidence-key-1",
        ),
    )

    assert attestation.verification_status == "VALID"
    assert attestation.public_key_fingerprint
    assert private_pem.decode() not in json.dumps(
        {
            "statement": attestation.statement,
            "public_key": attestation.public_key,
            "signature": attestation.signature,
        }
    )

    tampered = dict(attestation.statement)
    tampered["predicate"] = {"approved": False}
    attestation.statement = tampered
    db.commit()
    result = service.verify_attestation(attestation)
    assert result.status == "INVALID"
    assert db.get(SupplyChainAttestation, attestation.id).verification_status == "INVALID"


def test_hmac_evidence_verification_detects_manifest_tampering(
    db, tmp_path, monkeypatch
):
    monkeypatch.setenv("SACM_EVIDENCE_HMAC_KEY", "legacy-test-key")
    run = _run(db)
    service = EvidenceService(db, root=str(tmp_path))
    pack = service.build(run.id)

    assert service.verify(run.id, pack.id).status == "VALID"
    manifest = tmp_path / run.id / "run-manifest.json"
    content = json.loads(manifest.read_text(encoding="utf-8"))
    content["status"] = "TAMPERED"
    manifest.write_text(json.dumps(content), encoding="utf-8")

    result = service.verify(run.id, pack.id)
    assert result.status == "INVALID"
    assert any("manifest" in error.lower() for error in result.errors)


def test_legacy_hmac_evidence_signature_remains_verifiable(
    db, tmp_path, monkeypatch
):
    key = "legacy-test-key"
    monkeypatch.setenv("SACM_EVIDENCE_HMAC_KEY", key)
    run = _run(db)
    service = EvidenceService(db, root=str(tmp_path))
    pack = service.build(run.id)
    directory = tmp_path / run.id
    manifest = (directory / "run-manifest.json").read_bytes()
    (directory / "signature.sig").write_text(
        json.dumps(
            {
                "algorithm": "hmac-sha256",
                "signed_file": "run-manifest.json",
                "signature": hmac.new(
                    key.encode(), manifest, hashlib.sha256
                ).hexdigest(),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (directory / "checksums.sha256").write_text(
        service._checksums(directory), encoding="utf-8"
    )
    pack.pack_hash = None
    pack.previous_pack_hash = None
    pack.signature_algorithm = None
    pack.signature_key_id = None
    pack.signature = None
    db.commit()

    result = service.verify(run.id, pack.id)
    assert result.status == "VALID"
    assert result.algorithm == "hmac-sha256"


def test_secret_scan_ingest_never_persists_discovered_secret(db, monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "raw-secret-value")
    run = _run(db)
    content = {
        "scanner": "trivy",
        "findings": [
            {
                "rule": "credential",
                "secret": "raw-secret-value",
                "message": "token=raw-secret-value",
            }
        ],
    }
    record = SupplyChainService(db).ingest(
        run.id,
        SupplyChainRecordCreateV1(
            record_type="secret_scan",
            format="trivy-json",
            subject=_subject(),
            artifact_sha256=canonical_sha256(content),
            status="PASSED",
            coverage={"files": 10},
            content=content,
            metadata={"tool": "trivy"},
        ),
    )

    serialized = json.dumps(
        {"content": record.content, "metadata": record.metadata_}
    )
    assert "raw-secret-value" not in serialized
    assert "[REDACTED]" in serialized


def test_production_completion_reports_mandatory_supply_chain_gaps(
    db, tmp_path, monkeypatch
):
    run = _run(db)
    EvidenceService(db, root=str(tmp_path)).build(run.id)
    monkeypatch.setenv("SACM_ENVIRONMENT", "production")

    with pytest.raises(ValueError, match="mandatory supply-chain evidence"):
        RunService(db)._validate_completion(run)

    db.refresh(run)
    assert run.supply_chain_status == "INCOMPLETE"
    assert "secret_scan" in run.missing_supply_chain_evidence


def test_supply_chain_api_requires_authentication():
    with TestClient(app) as client:
        response = client.get("/v1/runs/not-real/supply-chain/completeness")

    assert response.status_code == 401

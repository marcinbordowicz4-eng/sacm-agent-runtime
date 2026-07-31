from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from sacm.core.auth_service import require_authenticated_actor
from sacm.core.supply_chain_service import SupplyChainService
from sacm.core.tenancy_service import AuthorizationError, ResourceAuthorizationService
from sacm.infrastructure.db.models import (
    SupplyChainAttestation,
    SupplyChainRecord,
)
from sacm.infrastructure.db.session import get_db
from sacm.schemas.supply_chain import (
    AttestationCreateV1,
    ImageCreateV1,
    ProvenanceCreateV1,
    ReleaseCreateV1,
    SupplyChainCompletenessV1,
    SupplyChainRecordCreateV1,
    SupplyChainRecordV1,
    VerificationResultV1,
)

router = APIRouter()


def _authorize(db: Session, run_id: str, actor: str, permission: str) -> None:
    try:
        ResourceAuthorizationService(db).require_run(run_id, actor, permission)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/runs/{run_id}/supply-chain/records",
    response_model=SupplyChainRecordV1,
    status_code=201,
)
def ingest_record(
    run_id: str,
    payload: SupplyChainRecordCreateV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> SupplyChainRecordV1:
    _authorize(db, run_id, actor, "evidence.build")
    try:
        return SupplyChainRecordV1.model_validate(
            SupplyChainService(db).ingest(run_id, payload)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/runs/{run_id}/supply-chain/records",
    response_model=list[SupplyChainRecordV1],
)
def list_records(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[SupplyChainRecordV1]:
    _authorize(db, run_id, actor, "evidence.read")
    records = (
        db.query(SupplyChainRecord)
        .filter(SupplyChainRecord.run_id == run_id)
        .order_by(SupplyChainRecord.created_at, SupplyChainRecord.id)
        .all()
    )
    return [SupplyChainRecordV1.model_validate(record) for record in records]


@router.post(
    "/runs/{run_id}/supply-chain/provenance",
    response_model=SupplyChainRecordV1,
    status_code=201,
)
def create_provenance(
    run_id: str,
    payload: ProvenanceCreateV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> SupplyChainRecordV1:
    _authorize(db, run_id, actor, "evidence.build")
    try:
        return SupplyChainRecordV1.model_validate(
            SupplyChainService(db).create_provenance(run_id, payload)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/supply-chain/images", status_code=201)
def create_image(
    run_id: str,
    payload: ImageCreateV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    _authorize(db, run_id, actor, "evidence.build")
    try:
        image = SupplyChainService(db).create_image(run_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "id": image.id,
        "schema_version": image.schema_version,
        "name": image.name,
        "digest": image.digest,
    }


@router.post("/runs/{run_id}/supply-chain/releases", status_code=201)
def create_release(
    run_id: str,
    payload: ReleaseCreateV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    _authorize(db, run_id, actor, "evidence.build")
    try:
        release = SupplyChainService(db).create_release(run_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "id": release.id,
        "schema_version": release.schema_version,
        "name": release.name,
        "version": release.version,
        "digest": release.digest,
    }


@router.post("/runs/{run_id}/supply-chain/attestations", status_code=201)
def create_attestation(
    run_id: str,
    payload: AttestationCreateV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    _authorize(db, run_id, actor, "evidence.build")
    try:
        attestation = SupplyChainService(db).attest(run_id, payload)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _attestation(attestation)


@router.post(
    "/runs/{run_id}/supply-chain/attestations/{attestation_id}/verify",
    response_model=VerificationResultV1,
)
def verify_attestation(
    run_id: str,
    attestation_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> VerificationResultV1:
    _authorize(db, run_id, actor, "evidence.read")
    attestation = db.get(SupplyChainAttestation, attestation_id)
    if attestation is None or attestation.run_id != run_id:
        raise HTTPException(status_code=404, detail="Attestation not found.")
    return SupplyChainService(db).verify_attestation(attestation)


@router.post(
    "/runs/{run_id}/supply-chain/attestations/verify-chain",
    response_model=VerificationResultV1,
)
def verify_attestation_chain(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> VerificationResultV1:
    _authorize(db, run_id, actor, "evidence.read")
    return SupplyChainService(db).verify_chain(run_id)


@router.get(
    "/runs/{run_id}/supply-chain/completeness",
    response_model=SupplyChainCompletenessV1,
)
def completeness(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> SupplyChainCompletenessV1:
    _authorize(db, run_id, actor, "evidence.read")
    return SupplyChainService(db).refresh_completeness(run_id)


def _attestation(attestation: SupplyChainAttestation) -> dict:
    return {
        "id": attestation.id,
        "schema_version": attestation.schema_version,
        "run_id": attestation.run_id,
        "subject": {
            "name": attestation.subject_name,
            "digest": attestation.subject_digest,
        },
        "predicate_type": attestation.predicate_type,
        "statement_hash": attestation.statement_hash,
        "attestation_hash": attestation.attestation_hash,
        "signature_algorithm": attestation.signature_algorithm,
        "signature_key_id": attestation.signature_key_id,
        "public_key_fingerprint": attestation.public_key_fingerprint,
        "verification_status": attestation.verification_status,
    }

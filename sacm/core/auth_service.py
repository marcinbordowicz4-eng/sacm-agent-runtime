import hashlib
import os
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import ServiceCredential
from sacm.infrastructure.db.session import get_db


@dataclass(frozen=True)
class Identity:
    subject: str
    issuer: str
    actor_type: str = "user"
    service_credential_id: str | None = None


@dataclass(frozen=True)
class RequestAuthContext:
    actor_id: str
    actor_type: str
    service_credential_id: str | None
    correlation_id: str | None


_AUTH_CONTEXT: ContextVar[RequestAuthContext | None] = ContextVar(
    "sacm_auth_context", default=None
)


def current_auth_context() -> RequestAuthContext | None:
    return _AUTH_CONTEXT.get()


def hash_service_token(token: str) -> str:
    return hashlib.sha256(f"sacm:service-token:v1:{token}".encode()).hexdigest()


class OIDCAuthenticator:
    """Validates bearer JWTs against a configured OIDC issuer and JWKS endpoint."""

    def authenticate(self, authorization: str | None) -> Identity:
        if not authorization or not authorization.startswith("Bearer "):
            raise PermissionError("Bearer authentication is required.")
        issuer = _required("SACM_OIDC_ISSUER")
        audience = _required("SACM_OIDC_AUDIENCE")
        jwks_url = os.getenv("SACM_OIDC_JWKS_URL", f"{issuer.rstrip('/')}/.well-known/jwks.json")
        token = authorization.removeprefix("Bearer ").strip()
        try:
            import jwt
        except ImportError as exc:
            raise RuntimeError("OIDC support requires: pip install -e '.[auth]'") from exc
        signing_key = jwt.PyJWKClient(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=audience,
            issuer=issuer,
        )
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise PermissionError("OIDC token does not include a subject.")
        return Identity(subject=subject, issuer=issuer)


def actor_from_request(
    authorization: str | None, development_actor: str | None
) -> str:
    if os.getenv("SACM_AUTH_REQUIRED", "false").lower() == "true":
        return OIDCAuthenticator().authenticate(authorization).subject
    if not development_actor:
        raise PermissionError("X-SACM-Actor is required in local authentication mode.")
    return development_actor


def production_mode() -> bool:
    return os.getenv("SACM_ENVIRONMENT", "development").lower() == "production"


def require_authenticated_actor(
    request: Request,
    authorization: str | None = Header(default=None),
    actor_id: str | None = Header(default=None, alias="X-SACM-Actor"),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
    db: Session = Depends(get_db),
) -> str:
    try:
        identity = authenticate_request(authorization, actor_id, db)
        _AUTH_CONTEXT.set(
            RequestAuthContext(
                actor_id=identity.subject,
                actor_type=identity.actor_type,
                service_credential_id=identity.service_credential_id,
                correlation_id=correlation_id or request.headers.get("X-Request-ID"),
            )
        )
        return identity.subject
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def authenticate_request(
    authorization: str | None,
    development_actor: str | None,
    db: Session,
) -> Identity:
    token = (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else None
    )
    if token and token.startswith("sacm_service_"):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        credential = (
            db.query(ServiceCredential)
            .filter(ServiceCredential.token_hash == hash_service_token(token))
            .first()
        )
        if (
            credential is None
            or credential.revoked_at is not None
            or (
                credential.expires_at is not None
                and credential.expires_at <= now
            )
        ):
            raise PermissionError("Service credential is invalid, expired, or revoked.")
        credential.last_used_at = now
        db.commit()
        return Identity(
            subject=f"service:{credential.id}",
            issuer="sacm-service-credentials",
            actor_type="service",
            service_credential_id=credential.id,
        )
    actor = actor_from_request(authorization, development_actor)
    return Identity(subject=actor, issuer="oidc" if production_mode() else "local")


def require_legacy_api_enabled() -> None:
    if production_mode() and os.getenv(
        "SACM_LEGACY_API_ENABLED", "false"
    ).lower() != "true":
        raise HTTPException(
            status_code=404,
            detail="The legacy API is disabled in production.",
        )


def require_direct_action_api_enabled() -> None:
    if production_mode() and os.getenv(
        "SACM_DIRECT_ACTION_API_ENABLED", "false"
    ).lower() != "true":
        raise HTTPException(
            status_code=404,
            detail="Direct action APIs are disabled in production.",
        )


def validate_production_configuration() -> None:
    if not production_mode():
        return
    errors: list[str] = []
    if os.getenv("SACM_AUTH_REQUIRED", "false").lower() != "true":
        errors.append("SACM_AUTH_REQUIRED=true")
    if not os.getenv("SACM_OIDC_ISSUER"):
        errors.append("SACM_OIDC_ISSUER")
    if not os.getenv("SACM_OIDC_AUDIENCE"):
        errors.append("SACM_OIDC_AUDIENCE")
    if os.getenv("DATABASE_URL", "").startswith("sqlite"):
        errors.append("a PostgreSQL DATABASE_URL")
    if not os.getenv("SACM_OPA_URL"):
        errors.append("SACM_OPA_URL")
    if os.getenv("SACM_OPA_FAIL_CLOSED", "true").lower() != "true":
        errors.append("SACM_OPA_FAIL_CLOSED=true")
    if not (
        os.getenv("SACM_EVIDENCE_SIGNING_PRIVATE_KEY_FILE")
        or os.getenv("SACM_EVIDENCE_HMAC_KEY_FILE")
        or os.getenv("SACM_EVIDENCE_HMAC_KEY")
    ):
        errors.append(
            "SACM_EVIDENCE_SIGNING_PRIVATE_KEY_FILE "
            "(preferred) or an evidence HMAC key"
        )
    if not (
        os.getenv("SACM_AUDIT_EXPORT_SIGNING_PRIVATE_KEY")
        or os.getenv("SACM_AUDIT_EXPORT_SIGNING_PRIVATE_KEY_FILE")
    ):
        errors.append(
            "SACM_AUDIT_EXPORT_SIGNING_PRIVATE_KEY or "
            "SACM_AUDIT_EXPORT_SIGNING_PRIVATE_KEY_FILE"
        )
    if os.getenv("SACM_LEGACY_API_ENABLED", "false").lower() == "true":
        errors.append("SACM_LEGACY_API_ENABLED=false")
    if os.getenv("SACM_DIRECT_ACTION_API_ENABLED", "false").lower() == "true":
        errors.append("SACM_DIRECT_ACTION_API_ENABLED=false")
    if os.getenv("SACM_WORKFLOW_BACKEND", "local").lower() == "local":
        errors.append("SACM_WORKFLOW_BACKEND=remote or temporal")
    if not (
        os.getenv("SACM_JOB_SIGNING_PRIVATE_KEY")
        or os.getenv("SACM_JOB_SIGNING_PRIVATE_KEY_FILE")
    ):
        errors.append(
            "SACM_JOB_SIGNING_PRIVATE_KEY or SACM_JOB_SIGNING_PRIVATE_KEY_FILE"
        )
    approved_sandboxes = {
        value.strip()
        for value in os.getenv(
            "SACM_APPROVED_SANDBOX_RUNTIMES", "runsc"
        ).split(",")
        if value.strip()
    }
    if not approved_sandboxes or "runc" in approved_sandboxes:
        errors.append(
            "SACM_APPROVED_SANDBOX_RUNTIMES with runsc or a stronger approved runtime"
        )
    secret_provider = os.getenv("SACM_SECRET_PROVIDER", "environment").lower()
    approved_secret_providers = {
        value.strip().lower()
        for value in os.getenv("SACM_APPROVED_SECRET_PROVIDERS", "").split(",")
        if value.strip()
    }
    if secret_provider == "environment":
        errors.append("SACM_SECRET_PROVIDER set to a non-environment provider")
    if secret_provider not in approved_secret_providers:
        errors.append(
            "SACM_APPROVED_SECRET_PROVIDERS including SACM_SECRET_PROVIDER"
        )
    if not os.getenv("SACM_BACKUP_ROOT"):
        errors.append("SACM_BACKUP_ROOT")
    if not os.getenv("SACM_BACKUP_AGE_RECIPIENTS_FILE"):
        errors.append("SACM_BACKUP_AGE_RECIPIENTS_FILE")
    if not os.getenv("SACM_BACKUP_AGE_IDENTITY_FILE"):
        errors.append("SACM_BACKUP_AGE_IDENTITY_FILE")
    if not os.getenv("SACM_BACKUP_ENCRYPTION_KEY_ID"):
        errors.append("SACM_BACKUP_ENCRYPTION_KEY_ID")
    if not (
        os.getenv("SACM_DESTRUCTIVE_RESTORE_GUARD")
        or os.getenv("SACM_DESTRUCTIVE_RESTORE_GUARD_FILE")
    ):
        errors.append("SACM_DESTRUCTIVE_RESTORE_GUARD")
    if errors:
        raise RuntimeError(
            "Production configuration is unsafe or incomplete; require: "
            + ", ".join(errors)
        )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be configured when OIDC authentication is enabled.")
    return value

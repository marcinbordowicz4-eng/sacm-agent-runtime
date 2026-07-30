import os
from dataclasses import dataclass

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class Identity:
    subject: str
    issuer: str


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
    authorization: str | None = Header(default=None),
    actor_id: str | None = Header(default=None, alias="X-SACM-Actor"),
) -> str:
    try:
        return actor_from_request(authorization, actor_id)
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


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
    if not os.getenv("SACM_EVIDENCE_HMAC_KEY"):
        errors.append("SACM_EVIDENCE_HMAC_KEY")
    if os.getenv("SACM_LEGACY_API_ENABLED", "false").lower() == "true":
        errors.append("SACM_LEGACY_API_ENABLED=false")
    if os.getenv("SACM_DIRECT_ACTION_API_ENABLED", "false").lower() == "true":
        errors.append("SACM_DIRECT_ACTION_API_ENABLED=false")
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

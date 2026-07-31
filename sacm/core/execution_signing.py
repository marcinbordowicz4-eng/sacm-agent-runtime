import base64
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from sacm.core.auth_service import production_mode


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def public_key_fingerprint(public_key_pem: str) -> str:
    serialization, ed25519, _ = _crypto()
    public_key = serialization.load_pem_public_key(public_key_pem.encode())
    if not isinstance(public_key, ed25519.Ed25519PublicKey):
        raise ValueError("Signing keys must be Ed25519 public keys.")
    der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


def verify_ed25519(public_key_pem: str, value: Any, signature: str) -> None:
    serialization, ed25519, invalid_signature = _crypto()
    public_key = serialization.load_pem_public_key(public_key_pem.encode())
    if not isinstance(public_key, ed25519.Ed25519PublicKey):
        raise ValueError("Executor signing keys must be Ed25519 public keys.")
    try:
        public_key.verify(base64.b64decode(signature, validate=True), canonical_json(value))
    except (ValueError, invalid_signature) as exc:
        raise ValueError("Result signature verification failed.") from exc


def sign_control_plane_payload(value: Any) -> tuple[str, dict[str, str]]:
    serialization, ed25519, _ = _crypto()
    private_key = _control_plane_private_key(
        os.getenv("SACM_JOB_SIGNING_PRIVATE_KEY"),
        os.getenv("SACM_JOB_SIGNING_PRIVATE_KEY_FILE"),
        production_mode(),
    )
    signature = base64.b64encode(private_key.sign(canonical_json(value))).decode()
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return signature, {
        "algorithm": "Ed25519",
        "key_fingerprint": public_key_fingerprint(public_pem),
        "public_key": public_pem,
    }


@lru_cache(maxsize=8)
def _control_plane_private_key(
    value: str | None, file_name: str | None, production: bool
) -> Any:
    serialization, ed25519, _ = _crypto()
    if file_name:
        value = Path(file_name).read_text()
    if value:
        value = value.replace("\\n", "\n")
        private_key = serialization.load_pem_private_key(value.encode(), password=None)
        if not isinstance(private_key, ed25519.Ed25519PrivateKey):
            raise RuntimeError("SACM job signing key must be an Ed25519 private key.")
        return private_key
    if production:
        raise RuntimeError(
            "SACM_JOB_SIGNING_PRIVATE_KEY or SACM_JOB_SIGNING_PRIVATE_KEY_FILE "
            "is required for remote execution."
        )
    return ed25519.Ed25519PrivateKey.generate()


def reset_signing_key_cache() -> None:
    _control_plane_private_key.cache_clear()


def _crypto() -> tuple[Any, Any, type[Exception]]:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:
        raise RuntimeError(
            "Signed execution contracts require: pip install -e '.[auth]'"
        ) from exc
    return serialization, ed25519, InvalidSignature

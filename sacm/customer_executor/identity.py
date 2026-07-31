from __future__ import annotations

import base64
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sacm.core.execution_signing import canonical_json, public_key_fingerprint


class IdentityStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.private_key_path = root / "executor-ed25519.pem"
        self.token_path = root / "auth-token"
        self.metadata_path = root / "executor.json"
        self.drain_path = root / "drain"

    def initialize(self) -> None:
        if self.root.is_symlink():
            raise PermissionError("Executor identity directory cannot be a symlink.")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        if not self.private_key_path.exists():
            self._write_private_key(Ed25519PrivateKey.generate())
        self.assert_secure_permissions()

    def assert_secure_permissions(self) -> None:
        if stat.S_IMODE(self.root.stat().st_mode) & 0o077:
            raise PermissionError("Executor identity directory permissions must be 0700.")
        for path in (self.private_key_path, self.token_path):
            if path.is_symlink():
                raise PermissionError(f"{path.name} cannot be a symlink.")
            if path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise PermissionError(f"{path.name} permissions must be 0600.")

    def private_key(self) -> Ed25519PrivateKey:
        value = serialization.load_pem_private_key(
            self.private_key_path.read_bytes(), password=None
        )
        if not isinstance(value, Ed25519PrivateKey):
            raise ValueError("Executor identity key is not Ed25519.")
        return value

    def public_key_pem(self) -> str:
        return self.private_key().public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def fingerprint(self) -> str:
        return public_key_fingerprint(self.public_key_pem())

    def sign(self, value: Any) -> str:
        return base64.b64encode(self.private_key().sign(canonical_json(value))).decode()

    def rotate_key(self) -> str:
        self._write_private_key(Ed25519PrivateKey.generate())
        return self.public_key_pem()

    def rotate_with(
        self, register: Callable[[str, str], dict[str, Any]]
    ) -> dict[str, Any]:
        candidate = Ed25519PrivateKey.generate()
        public_key = candidate.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        response = register(public_key, public_key_fingerprint(public_key))
        self._write_private_key(candidate)
        return response

    def write_token(self, token: str) -> None:
        if not token:
            raise ValueError("Executor auth token cannot be empty.")
        self._atomic_write(self.token_path, token.encode(), 0o600)

    def token(self) -> str:
        self.assert_secure_permissions()
        return self.token_path.read_text(encoding="utf-8").strip()

    def write_metadata(self, value: dict[str, Any]) -> None:
        self._atomic_write(
            self.metadata_path,
            json.dumps(value, sort_keys=True, indent=2).encode(),
            0o600,
        )

    def metadata(self) -> dict[str, Any]:
        if not self.metadata_path.exists():
            return {}
        return json.loads(self.metadata_path.read_text(encoding="utf-8"))

    def set_drain(self, enabled: bool, reason: str = "") -> None:
        if enabled:
            self._atomic_write(self.drain_path, reason.encode(), 0o600)
        else:
            self.drain_path.unlink(missing_ok=True)

    @property
    def draining(self) -> bool:
        return self.drain_path.exists()

    def _write_private_key(self, key: Ed25519PrivateKey) -> None:
        self._atomic_write(
            self.private_key_path,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            0o600,
        )

    def _atomic_write(self, path: Path, value: bytes, mode: int) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, mode)
        finally:
            temporary.unlink(missing_ok=True)

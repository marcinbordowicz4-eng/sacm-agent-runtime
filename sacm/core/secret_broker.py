import base64
import hashlib
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from sacm.schemas.execution_plan import SecretReferenceV1, SecretRequestV1

_PROVIDER_NAMES = {
    "environment",
    "vault",
    "aws-secrets-manager",
    "aws-sts",
    "azure-key-vault",
    "azure-managed-identity",
}
_SENSITIVE_CONFIG_PARTS = {
    "authorization",
    "credential",
    "password",
    "private",
    "secret",
    "token",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SecretProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderCredential:
    value: bytes = field(repr=False)
    content_type: str = "application/octet-stream"
    expires_at: datetime | None = None
    provider_lease_id: str | None = field(default=None, repr=False)


class SecretProvider(ABC):
    name: str

    @abstractmethod
    def validate_config(self, config: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def health(self, config: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def fetch(
        self,
        request: SecretRequestV1,
        config: dict[str, Any],
        *,
        ttl_seconds: int,
    ) -> ProviderCredential:
        raise NotImplementedError

    def revoke(
        self, config: dict[str, Any], provider_lease_id: str
    ) -> bool:
        return False

    def renew(
        self,
        config: dict[str, Any],
        provider_lease_id: str,
        *,
        ttl_seconds: int,
    ) -> datetime | None:
        return None


class EnvironmentSecretProvider(SecretProvider):
    name = "environment"

    def validate_config(self, config: dict[str, Any]) -> None:
        if config:
            raise SecretProviderError(
                "The environment provider does not accept stored configuration."
            )

    def health(self, config: dict[str, Any]) -> dict[str, Any]:
        self.validate_config(config)
        return {"healthy": True, "mode": "process-environment"}

    def fetch(
        self,
        request: SecretRequestV1,
        config: dict[str, Any],
        *,
        ttl_seconds: int,
    ) -> ProviderCredential:
        self.validate_config(config)
        variable = request.environment_variable
        if not variable:
            raise SecretProviderError("An environment variable is required.")
        value = os.environ.get(variable)
        if value is None:
            raise SecretProviderError(
                f"Environment secret reference {variable!r} is unavailable."
            )
        return ProviderCredential(
            value=value.encode(),
            content_type="text/plain; charset=utf-8",
            expires_at=_utcnow() + timedelta(seconds=ttl_seconds),
        )


class VaultSecretProvider(SecretProvider):
    name = "vault"

    def validate_config(self, config: dict[str, Any]) -> None:
        _require_config(config, "address")
        _require_config(config, "token_env")

    def health(self, config: dict[str, Any]) -> dict[str, Any]:
        self.validate_config(config)
        address = str(config["address"]).rstrip("/")
        try:
            response = httpx.get(
                f"{address}/v1/sys/health",
                timeout=float(config.get("timeout_seconds", 5)),
            )
        except httpx.HTTPError as exc:
            return {"healthy": False, "error": type(exc).__name__}
        return {
            "healthy": response.status_code in {200, 429, 472, 473},
            "status_code": response.status_code,
        }

    def fetch(
        self,
        request: SecretRequestV1,
        config: dict[str, Any],
        *,
        ttl_seconds: int,
    ) -> ProviderCredential:
        self.validate_config(config)
        token = os.environ.get(str(config["token_env"]))
        if not token:
            raise SecretProviderError(
                "Vault credential environment reference is unavailable."
            )
        headers = {"X-Vault-Token": token}
        namespace_env = config.get("namespace_env")
        if namespace_env and os.environ.get(str(namespace_env)):
            headers["X-Vault-Namespace"] = os.environ[str(namespace_env)]
        address = str(config["address"]).rstrip("/")
        resource = str(request.resource).lstrip("/")
        try:
            response = httpx.get(
                f"{address}/v1/{resource}",
                headers=headers,
                timeout=float(config.get("timeout_seconds", 10)),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SecretProviderError(
                f"Vault credential retrieval failed: {type(exc).__name__}."
            ) from exc
        payload = response.json()
        data = payload.get("data", {})
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        value_field = config.get("value_field")
        value = data.get(value_field) if value_field and isinstance(data, dict) else data
        encoded, content_type = _encode_provider_value(value)
        lease_duration = int(payload.get("lease_duration") or ttl_seconds)
        return ProviderCredential(
            value=encoded,
            content_type=content_type,
            expires_at=_utcnow() + timedelta(
                seconds=max(1, min(ttl_seconds, lease_duration))
            ),
            provider_lease_id=payload.get("lease_id") or None,
        )

    def revoke(
        self, config: dict[str, Any], provider_lease_id: str
    ) -> bool:
        response = self._lease_request(
            config,
            "revoke",
            {"lease_id": provider_lease_id},
        )
        return response.status_code in {200, 204}

    def renew(
        self,
        config: dict[str, Any],
        provider_lease_id: str,
        *,
        ttl_seconds: int,
    ) -> datetime | None:
        response = self._lease_request(
            config,
            "renew",
            {"lease_id": provider_lease_id, "increment": ttl_seconds},
        )
        if response.status_code != 200:
            return None
        duration = int(response.json().get("lease_duration") or ttl_seconds)
        return _utcnow() + timedelta(seconds=duration)

    def _lease_request(
        self,
        config: dict[str, Any],
        operation: str,
        payload: dict[str, Any],
    ) -> httpx.Response:
        self.validate_config(config)
        token = os.environ.get(str(config["token_env"]))
        if not token:
            raise SecretProviderError(
                "Vault credential environment reference is unavailable."
            )
        try:
            return httpx.put(
                f"{str(config['address']).rstrip('/')}/v1/sys/leases/{operation}",
                headers={"X-Vault-Token": token},
                json=payload,
                timeout=float(config.get("timeout_seconds", 10)),
            )
        except httpx.HTTPError as exc:
            raise SecretProviderError(
                f"Vault lease {operation} failed: {type(exc).__name__}."
            ) from exc


class AwsSecretProvider(SecretProvider):
    def __init__(self, name: str) -> None:
        self.name = name

    def validate_config(self, config: dict[str, Any]) -> None:
        if "region" in config and not str(config["region"]).strip():
            raise SecretProviderError("AWS region cannot be empty.")
        if self.name == "aws-sts":
            _require_config(config, "role_arn")
        self._boto3()

    def health(self, config: dict[str, Any]) -> dict[str, Any]:
        try:
            self.validate_config(config)
            boto3 = self._boto3()
            kwargs = (
                {"region_name": config.get("region")}
                if config.get("region")
                else {}
            )
            boto3.client("sts", **kwargs).get_caller_identity()
        except SecretProviderError as exc:
            return {"healthy": False, "error": str(exc)}
        except Exception as exc:
            return {"healthy": False, "error": type(exc).__name__}
        return {"healthy": True, "credential_chain": "boto3-default"}

    def fetch(
        self,
        request: SecretRequestV1,
        config: dict[str, Any],
        *,
        ttl_seconds: int,
    ) -> ProviderCredential:
        self.validate_config(config)
        boto3 = self._boto3()
        kwargs = {"region_name": config.get("region")} if config.get("region") else {}
        if self.name == "aws-secrets-manager":
            response = boto3.client("secretsmanager", **kwargs).get_secret_value(
                SecretId=request.resource
            )
            if "SecretString" in response:
                value = str(response["SecretString"]).encode()
                content_type = "text/plain; charset=utf-8"
            else:
                value = base64.b64decode(response["SecretBinary"])
                content_type = "application/octet-stream"
            return ProviderCredential(
                value=value,
                content_type=content_type,
                expires_at=_utcnow() + timedelta(seconds=ttl_seconds),
                provider_lease_id=response.get("VersionId"),
            )
        duration = max(900, min(ttl_seconds, 3600))
        assume: dict[str, Any] = {
            "RoleArn": config["role_arn"],
            "RoleSessionName": "sacm-executor",
            "DurationSeconds": duration,
        }
        if request.permissions:
            assume["Policy"] = json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": request.permissions,
                            "Resource": request.resource,
                        }
                    ],
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        response = boto3.client("sts", **kwargs).assume_role(**assume)
        credentials = response["Credentials"]
        expiration = credentials["Expiration"]
        if getattr(expiration, "tzinfo", None):
            expiration = expiration.astimezone(timezone.utc).replace(tzinfo=None)
        encoded = json.dumps(
            {
                "access_key_id": credentials["AccessKeyId"],
                "secret_access_key": credentials["SecretAccessKey"],
                "session_token": credentials["SessionToken"],
                "expiration": expiration.isoformat(),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return ProviderCredential(
            value=encoded,
            content_type="application/json",
            expires_at=expiration,
            provider_lease_id=response.get("ResponseMetadata", {}).get("RequestId"),
        )

    @staticmethod
    def _boto3() -> Any:
        try:
            import boto3
        except ImportError as exc:
            raise SecretProviderError(
                "AWS secret providers require: pip install -e '.[secrets-aws]'"
            ) from exc
        return boto3


class AzureSecretProvider(SecretProvider):
    def __init__(self, name: str) -> None:
        self.name = name

    def validate_config(self, config: dict[str, Any]) -> None:
        if self.name == "azure-key-vault":
            _require_config(config, "vault_url")
        self._credential_class()

    def health(self, config: dict[str, Any]) -> dict[str, Any]:
        try:
            self.validate_config(config)
            self._credential_class()().get_token(
                "https://management.azure.com/.default"
            )
        except SecretProviderError as exc:
            return {"healthy": False, "error": str(exc)}
        except Exception as exc:
            return {"healthy": False, "error": type(exc).__name__}
        return {"healthy": True, "credential_chain": "DefaultAzureCredential"}

    def fetch(
        self,
        request: SecretRequestV1,
        config: dict[str, Any],
        *,
        ttl_seconds: int,
    ) -> ProviderCredential:
        self.validate_config(config)
        credential = self._credential_class()()
        if self.name == "azure-managed-identity":
            scope = request.audience or request.resource
            token = credential.get_token(
                scope if str(scope).endswith("/.default") else f"{scope}/.default"
            )
            return ProviderCredential(
                value=token.token.encode(),
                content_type="text/plain; charset=utf-8",
                expires_at=datetime.fromtimestamp(
                    token.expires_on, timezone.utc
                ).replace(tzinfo=None),
            )
        try:
            from azure.keyvault.secrets import SecretClient
        except ImportError as exc:
            raise SecretProviderError(
                "Azure Key Vault requires: pip install -e '.[secrets-azure]'"
            ) from exc
        secret = SecretClient(
            vault_url=str(config["vault_url"]), credential=credential
        ).get_secret(str(request.resource))
        return ProviderCredential(
            value=secret.value.encode(),
            content_type="text/plain; charset=utf-8",
            expires_at=secret.properties.expires_on.replace(tzinfo=None)
            if secret.properties.expires_on
            else _utcnow() + timedelta(seconds=ttl_seconds),
            provider_lease_id=secret.properties.version,
        )

    @staticmethod
    def _credential_class() -> Any:
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:
            raise SecretProviderError(
                "Azure secret providers require: "
                "pip install -e '.[secrets-azure]'"
            ) from exc
        return DefaultAzureCredential


class SecretBroker(ABC):
    """Resolves value-free references and exchanges provider material in memory."""

    @abstractmethod
    def resolve(self, request: SecretRequestV1) -> SecretReferenceV1:
        raise NotImplementedError


class EnvironmentSecretBroker(SecretBroker):
    """Backward-compatible environment resolver with opaque, value-free handles."""

    def resolve(self, request: SecretRequestV1) -> SecretReferenceV1:
        if request.provider != "environment":
            raise SecretProviderError(
                "EnvironmentSecretBroker cannot resolve non-environment providers."
            )
        environment_variable = request.environment_variable
        if not environment_variable:
            raise SecretProviderError("An environment variable is required.")
        available = bool(os.environ.get(environment_variable))
        digest = hashlib.sha256(
            f"environment:{environment_variable}".encode()
        ).hexdigest()[:32]
        return SecretReferenceV1(
            request_name=request.name,
            handle=f"secret-ref:{digest}",
            source="environment",
            available=available,
            metadata={
                "environment_variable": environment_variable,
                "required": request.required,
            },
        )


class EnterpriseSecretBroker(SecretBroker):
    def __init__(self, providers: dict[str, SecretProvider] | None = None) -> None:
        self.providers = providers or default_secret_providers()

    def provider(self, name: str) -> SecretProvider:
        provider = self.providers.get(name)
        if provider is None:
            raise SecretProviderError(f"Unsupported secret provider: {name}.")
        return provider

    def resolve(self, request: SecretRequestV1) -> SecretReferenceV1:
        if request.provider == "environment":
            return EnvironmentSecretBroker().resolve(request)
        digest = hashlib.sha256(
            f"{request.provider}:{request.resource}:{request.audience}".encode()
        ).hexdigest()[:32]
        return SecretReferenceV1(
            request_name=request.name,
            handle=f"secret-ref:{digest}",
            source=request.provider,
            available=request.provider in self.providers,
            metadata={
                "resource_hash": hashlib.sha256(
                    str(request.resource).encode()
                ).hexdigest(),
                "permissions": sorted(set(request.permissions)),
                "audience": request.audience,
                "provider_config": request.provider_config,
                "required": request.required,
            },
        )

    def fetch(
        self,
        request: SecretRequestV1,
        config: dict[str, Any],
        *,
        ttl_seconds: int,
    ) -> ProviderCredential:
        return self.provider(request.provider).fetch(
            request, config, ttl_seconds=ttl_seconds
        )


def default_secret_providers() -> dict[str, SecretProvider]:
    return {
        "environment": EnvironmentSecretProvider(),
        "vault": VaultSecretProvider(),
        "aws-secrets-manager": AwsSecretProvider("aws-secrets-manager"),
        "aws-sts": AwsSecretProvider("aws-sts"),
        "azure-key-vault": AzureSecretProvider("azure-key-vault"),
        "azure-managed-identity": AzureSecretProvider(
            "azure-managed-identity"
        ),
    }


def validate_provider_config(provider: str, config: dict[str, Any]) -> None:
    if provider not in _PROVIDER_NAMES:
        raise SecretProviderError(f"Unsupported secret provider: {provider}.")
    _reject_sensitive_config(config)
    default_secret_providers()[provider].validate_config(config)


def provider_lease_id_hash(provider: str, lease_id: str | None) -> str | None:
    if not lease_id:
        return None
    return hashlib.sha256(f"sacm:{provider}:lease:v1:{lease_id}".encode()).hexdigest()


def wrap_for_executor(public_key_pem: str, value: bytes) -> tuple[str, str, str]:
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise SecretProviderError(
            "Credential wrapping requires: pip install -e '.[auth]'"
        ) from exc
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode())
    except ValueError as exc:
        raise SecretProviderError("Executor encryption public key is invalid.") from exc
    if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 2048:
        raise SecretProviderError(
            "Credential wrapping requires an RSA public key of at least 2048 bits."
        )
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, value, None)
    encrypted_key = public_key.encrypt(
        key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    envelope = {
        "ciphertext": base64.b64encode(ciphertext).decode(),
        "encrypted_key": base64.b64encode(encrypted_key).decode(),
        "nonce": base64.b64encode(nonce).decode(),
    }
    return (
        base64.b64encode(
            json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()
        ).decode(),
        "RSA-OAEP-256+A256GCM",
        hashlib.sha256(der).hexdigest(),
    )


def encryption_key_fingerprint(public_key_pem: str) -> str:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError as exc:
        raise SecretProviderError(
            "Credential wrapping requires: pip install -e '.[auth]'"
        ) from exc
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode())
    except ValueError as exc:
        raise SecretProviderError("Executor encryption public key is invalid.") from exc
    if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 2048:
        raise SecretProviderError(
            "Credential wrapping requires an RSA public key of at least 2048 bits."
        )
    der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


def _require_config(config: dict[str, Any], key: str) -> None:
    if not str(config.get(key, "")).strip():
        raise SecretProviderError(f"Provider configuration requires {key!r}.")


def _reject_sensitive_config(value: Any, path: str = "config") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in _SENSITIVE_CONFIG_PARTS):
                if not normalized.endswith("_env"):
                    raise SecretProviderError(
                        f"{path}.{key} may only reference credentials by *_env name."
                    )
            _reject_sensitive_config(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_sensitive_config(item, f"{path}[{index}]")


def _encode_provider_value(value: Any) -> tuple[bytes, str]:
    if isinstance(value, str):
        return value.encode(), "text/plain; charset=utf-8"
    if value is None:
        raise SecretProviderError("Provider response did not contain credential material.")
    return (
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode(),
        "application/json",
    )

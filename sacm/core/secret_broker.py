import hashlib
import os
from abc import ABC, abstractmethod

from sacm.schemas.execution_plan import SecretReferenceV1, SecretRequestV1


class SecretBroker(ABC):
    """Resolves references without returning or persisting secret values."""

    @abstractmethod
    def resolve(self, request: SecretRequestV1) -> SecretReferenceV1:
        raise NotImplementedError


class EnvironmentSecretBroker(SecretBroker):
    """Exact environment-variable resolver with opaque, value-free handles."""

    def resolve(self, request: SecretRequestV1) -> SecretReferenceV1:
        environment_variable = request.environment_variable
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

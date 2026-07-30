from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class RepositoryConfigError(ValueError):
    """Raised when a repository's SACM configuration is unsafe or invalid."""


class RepositorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = "."
    default_branch: str = "main"


class CommandSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build: str | None = None
    test: str | None = None

    @field_validator("build", "test")
    @classmethod
    def validate_command(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.strip() or "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError("Commands must be non-empty, single-line strings.")
        return value


class RepositoryRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["sacm/v1"]
    repository: RepositorySettings = Field(default_factory=RepositorySettings)
    commands: CommandSettings = Field(default_factory=CommandSettings)
    constraints: list[str] = Field(default_factory=list)


def load_repository_config(repository_path: str | None) -> RepositoryRuntimeConfig | None:
    """Load optional, validated SACM configuration from a repository root."""
    if repository_path is None:
        return None
    root = Path(repository_path).resolve()
    config_path = root / ".sacm.yaml"
    if not config_path.is_file():
        return None
    try:
        content = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RepositoryConfigError(
            f"Could not read SACM configuration at {config_path}: {exc}"
        ) from exc
    if not isinstance(content, dict):
        raise RepositoryConfigError(
            f"SACM configuration at {config_path} must be a YAML mapping."
        )
    try:
        return RepositoryRuntimeConfig.model_validate(content)
    except ValidationError as exc:
        raise RepositoryConfigError(
            f"Invalid SACM configuration at {config_path}: {exc}"
        ) from exc

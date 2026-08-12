"""Configuration loading for MarkOS.

Configuration is read from the environment and, optionally, a TOML file. This
module deliberately has no code that creates or modifies the configured vault.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, field_validator

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class SettingsError(ValueError):
    """Raised when MarkOS configuration cannot be loaded."""


class Settings(BaseModel):
    """Validated runtime settings."""

    model_config = ConfigDict(frozen=True)

    vault_path: Path
    log_level: LogLevel = "INFO"

    @field_validator("vault_path", mode="before")
    @classmethod
    def expand_vault_path(cls, value: object) -> Path:
        return Path(str(value)).expanduser()

    @field_validator("log_level", mode="before")
    @classmethod
    def normalise_log_level(cls, value: object) -> str:
        return str(value).upper()

    @classmethod
    def load(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        config_path: Path | None = None,
    ) -> Settings:
        """Load settings, with environment variables overriding TOML values.

        Supported environment variables are ``MARKOS_CONFIG``,
        ``MARKOS_VAULT_PATH``, and ``MARKOS_LOG_LEVEL``. The optional TOML file
        uses a ``[markos]`` table.
        """

        env = os.environ if environ is None else environ
        resolved_config_path = config_path
        if resolved_config_path is None and env.get("MARKOS_CONFIG"):
            resolved_config_path = Path(env["MARKOS_CONFIG"]).expanduser()

        values: dict[str, Any] = {}
        if resolved_config_path is not None:
            values.update(_read_config_file(resolved_config_path))

        if env.get("MARKOS_VAULT_PATH"):
            values["vault_path"] = env["MARKOS_VAULT_PATH"]
        if env.get("MARKOS_LOG_LEVEL"):
            values["log_level"] = env["MARKOS_LOG_LEVEL"]

        if not values.get("vault_path"):
            raise SettingsError(
                "vault path is required; set MARKOS_VAULT_PATH or add vault_path "
                "to the [markos] configuration table"
            )

        try:
            return cls.model_validate(values)
        except ValueError as error:
            raise SettingsError(f"invalid MarkOS configuration: {error}") from error


def _read_config_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as config_file:
            document = tomllib.load(config_file)
    except FileNotFoundError as error:
        raise SettingsError(f"configuration file does not exist: {path}") from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SettingsError(f"could not read configuration file {path}: {error}") from error

    section = document.get("markos", {})
    if not isinstance(section, dict):
        raise SettingsError("the [markos] configuration value must be a table")

    values = cast(dict[str, Any], section.copy())
    vault_path = values.get("vault_path")
    if vault_path and not Path(str(vault_path)).expanduser().is_absolute():
        values["vault_path"] = path.parent / str(vault_path)
    return values

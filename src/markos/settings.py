"""Configuration loading for MarkOS.

Configuration is read from the environment and, optionally, a TOML file. This
module deliberately has no code that creates or modifies the configured Markdown root.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from markos._compat import warn_renamed

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class SettingsError(ValueError):
    """Raised when MarkOS configuration cannot be loaded."""


class Settings(BaseModel):
    """Validated runtime settings."""

    model_config = ConfigDict(frozen=True)

    markdown_root: Path | None = Field(
        default=None, validation_alias=AliasChoices("markdown_root", "vault_path")
    )
    index_path: Path | None = None
    log_level: LogLevel = "INFO"

    @property
    def vault_path(self) -> Path | None:
        """Deprecated read-only field spelling for existing Python clients."""
        warn_renamed("vault_path", "markdown_root")
        return self.markdown_root

    @field_validator("markdown_root", "index_path", mode="before")
    @classmethod
    def expand_path(cls, value: object) -> Path | None:
        if value is None:
            return None
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
        require_markdown_root: bool | None = None,
        require_vault: bool | None = None,
    ) -> Settings:
        """Load settings, with environment variables overriding TOML values.

        Supported environment variables are ``MARKOS_CONFIG``,
        ``MARKOS_MARKDOWN_ROOT``, ``MARKOS_INDEX_PATH``, and ``MARKOS_LOG_LEVEL``.
        The optional TOML file uses a ``[markos]`` table.
        """

        env = os.environ if environ is None else environ
        if require_vault is not None:
            warn_renamed("require_vault", "require_markdown_root")
        required = (
            require_markdown_root
            if require_markdown_root is not None
            else (require_vault if require_vault is not None else True)
        )
        resolved_config_path = config_path
        if resolved_config_path is None and env.get("MARKOS_CONFIG"):
            resolved_config_path = Path(env["MARKOS_CONFIG"]).expanduser()

        values: dict[str, Any] = {}
        if resolved_config_path is not None:
            values.update(_read_config_file(resolved_config_path))

        if env.get("MARKOS_VAULT_PATH") and not env.get("MARKOS_MARKDOWN_ROOT"):
            warn_renamed("MARKOS_VAULT_PATH", "MARKOS_MARKDOWN_ROOT")
            values["markdown_root"] = env["MARKOS_VAULT_PATH"]
        if env.get("MARKOS_MARKDOWN_ROOT"):
            values["markdown_root"] = env["MARKOS_MARKDOWN_ROOT"]
        if env.get("MARKOS_INDEX_PATH"):
            values["index_path"] = env["MARKOS_INDEX_PATH"]
        if env.get("MARKOS_LOG_LEVEL"):
            values["log_level"] = env["MARKOS_LOG_LEVEL"]

        if required and not values.get("markdown_root"):
            raise SettingsError(
                "markdown_root path is required; set MARKOS_MARKDOWN_ROOT or add markdown_root "
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
    if "vault_path" in values:
        old_value = values.pop("vault_path")
        if "markdown_root" not in values:
            warn_renamed("vault_path", "markdown_root")
            values["markdown_root"] = old_value
    for key in ("markdown_root", "index_path"):
        configured_path = values.get(key)
        if configured_path and not Path(str(configured_path)).expanduser().is_absolute():
            values[key] = path.parent / str(configured_path)
    return values

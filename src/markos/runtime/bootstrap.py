"""Fail-closed startup preflight; validates only, with no adapter or service launch."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pydantic import SecretStr

from markos.runtime.contracts import ContractError, ContractModel
from markos.runtime.settings import RuntimeConfigurationError, RuntimeSettings
from markos.runtime.topology import (
    MAX_MANIFEST_BYTES,
    PublisherDefinition,
    Subscription,
    TopologyManifest,
)


class RuntimeBootstrap(ContractModel):
    settings: RuntimeSettings
    topology: TopologyManifest
    broker_dsn: SecretStr
    database_dsn: SecretStr


def _dsn_valid(value: str, *, broker: bool, production: bool) -> bool:
    if any(char.isspace() for char in value):
        return False
    try:
        parsed = urlsplit(value)
        if not parsed.hostname or not parsed.username or not parsed.password or parsed.fragment:
            return False
        if parsed.port is not None and parsed.port <= 0:
            return False
        if broker:
            if parsed.scheme not in {"amqp", "amqps"} or parsed.query:
                return False
            secure = parsed.scheme == "amqps"
        else:
            if parsed.scheme not in {"postgres", "postgresql"} or parsed.path in {"", "/"}:
                return False
            options = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
            if (set(options) - {"sslmode"} or any(len(v) != 1 for v in options.values())
                    or options.get("sslmode", ["disable"])[0] not in {"disable", "verify-full"}):
                return False
            secure = options.get("sslmode") == ["verify-full"]
        return secure or (not production and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
    except ValueError:
        return False


def validate_startup(config_path: Path | None = None, *,
                     environ: Mapping[str, str] | None = None) -> RuntimeBootstrap:
    env = os.environ if environ is None else environ
    settings, topology_path = RuntimeSettings.load(config_path, environ=env)
    try:
        with topology_path.open("rb") as handle:
            topology = TopologyManifest.from_bytes(handle.read(MAX_MANIFEST_BYTES + 1))
        if topology.environment != settings.environment:
            raise ContractError("environment mismatch")
        definitions: tuple[PublisherDefinition | Subscription, ...]
        definitions = (
            topology.publishers if settings.role == "publisher" else topology.subscriptions
        )
        if not any(d.scope == settings.scope for d in definitions):
            raise ContractError("unregistered service")
        topology.validate_budget(settings.limits)
        broker = env.get(settings.broker_dsn_env, "")
        database = env.get(settings.database_dsn_env, "")
        production = settings.environment == "production"
        if not (_dsn_valid(broker, broker=True, production=production)
                and _dsn_valid(database, broker=False, production=production)):
            raise ContractError("missing or invalid credentials")
        return RuntimeBootstrap(settings=settings, topology=topology,
                                broker_dsn=SecretStr(broker), database_dsn=SecretStr(database))
    except (OSError, ValueError):
        raise RuntimeConfigurationError("runtime preflight failed") from None

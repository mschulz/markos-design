"""Services run the production profile only when confirmed, and then without any fixture."""

import asyncio
import json
import ssl
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")

from markos_runtime.api.application import SyntheticApi  # noqa: E402
from markos_runtime.api.identity import FixtureIssuer, IdentityUnavailable  # noqa: E402
from markos_runtime.api.mail_alerts import GOOGLE_KEYS_URL  # noqa: E402
from markos_runtime.cli import main  # noqa: E402
from markos_runtime.control.api_config import SyntheticGovernanceApiConfig  # noqa: E402
from markos_runtime.control.conversation import ConversationWorker  # noqa: E402
from markos_runtime.control_lifecycle.issuer import (  # noqa: E402
    RemoteFixtureIssuer,
    StoredIssuer,
)
from markos_runtime.control_lifecycle.production import (  # noqa: E402
    FileSecrets,
    ProductionBillApiConfig,
    ProductionServiceConfig,
)
from markos_runtime.control_lifecycle.service import ControlService  # noqa: E402
from markos_runtime.knowledge.spaces import SpacesObjects  # noqa: E402
from markos_runtime.lifecycle.service import FatalServiceError  # noqa: E402
from markos_runtime.mail.google import GOOGLE  # noqa: E402
from markos_runtime.store.database import StorageError  # noqa: E402

TOKEN = "t" * 43
KEY = b"k" * 32
ORIGIN = "https://markos.example.com"
HOST = "db.abcdef.supabase.co"
CA = "/etc/markos/supabase-ca.pem"
ROUTES = {
    "schema_version": 1,
    "conversation_profile": "deterministic-conversation-v1",
    "web_profile": "synthetic-web-cli-v1",
    "governance_profile": "synthetic-governance-v1",
}


def document(**changes: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "profile": "control-process-production-v1",
        "component": "gateway",
        "service_id": "control-gateway",
        "owner_id": "11111111-1111-4111-8111-111111111111",
        "domain": "personal",
        "api_origin": ORIGIN,
        "issuer_endpoint": "https://issuer:19444/fixture",
        "health_port": 8780,
        "ca_file": "/etc/markos/ca.pem",
        "certificate_file": "/etc/markos/server.pem",
        "private_key_file": "/etc/markos/key.pem",
        "database_host": HOST,
        "database_ca_file": CA,
        "broker_publisher_user": "markos-publisher",
        "spaces": {
            "endpoint": "https://syd1.digitaloceanspaces.com",
            "region": "syd1",
            "bucket": "markos",
        },
        **changes,
    }


def production(**changes: Any) -> ProductionServiceConfig:
    return ProductionServiceConfig.from_wire(json.dumps(document(**changes)).encode())


def bill_routes() -> ProductionBillApiConfig:
    return ProductionBillApiConfig.model_validate(
        {
            **ROUTES,
            "api_origin": ORIGIN,
            "bill_profile": "bill-text-v1",
            "production_profile": "control-process-production-v1",
        }
    )


def test_the_command_needs_confirmation_and_the_production_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markos_runtime.control_lifecycle.service as service

    seen: list[Any] = []

    def fake_run(config: Any, environ: Any) -> int:
        seen.append((config, environ))
        return 0

    monkeypatch.setattr(service, "run_control", fake_run)
    good = tmp_path / "production.json"
    good.write_text(json.dumps(document()))
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(document(profile="control-process-fixture-v1")))
    assert main(["serve-control-production", "--config", str(good)]) == 2
    assert main(["serve-control-production", "--config", str(fixture), "--confirm-production"]) == 2
    assert main(["serve-control-synthetic", "--config", str(good), "--confirm-synthetic"]) == 2
    assert seen == []
    assert main(["serve-control-production", "--config", str(good), "--confirm-production"]) == 0
    config, secrets = seen[0]
    # Secrets come from the mounted files, never from the environment.
    assert isinstance(config, ProductionServiceConfig) and isinstance(secrets, FileSecrets)
    assert secrets.directory == "/run/secrets"


def test_production_routes_need_a_public_origin() -> None:
    assert bill_routes().environment == "production"
    with pytest.raises(ValueError):
        ProductionBillApiConfig.model_validate(
            {
                **ROUTES,
                "api_origin": "https://127.0.0.1:18443",
                "bill_profile": "bill-text-v1",
                "production_profile": "control-process-production-v1",
            }
        )


@pytest.mark.parametrize(
    "production_routes,synthetic,confirmed",
    [
        (False, False, False),
        (False, True, True),
        (True, False, True),
        (True, True, False),
        (True, True, True),
    ],
)
def test_the_api_takes_exactly_one_matching_confirmation(
    production_routes: bool, synthetic: bool, confirmed: bool
) -> None:
    routes = (
        bill_routes()
        if production_routes
        else SyntheticGovernanceApiConfig.model_validate(
            {**ROUTES, "api_origin": "https://127.0.0.1:18443"}
        )
    )
    # Synthetic routes need confirm_synthetic only; production routes need confirm_production only.
    kwargs = {"confirm_synthetic": synthetic, "confirm_production": confirmed}
    if production_routes == confirmed and synthetic != confirmed:
        SyntheticApi(routes, object(), FixtureIssuer(), KEY, **kwargs)  # type: ignore[arg-type]
    else:
        with pytest.raises(ValueError):
            SyntheticApi(routes, object(), FixtureIssuer(), KEY, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("synthetic,confirmed", [(False, False), (True, True)])
def test_the_worker_refuses_no_or_both_confirmations(synthetic: bool, confirmed: bool) -> None:
    with pytest.raises(StorageError):
        ConversationWorker(
            object(),  # type: ignore[arg-type]
            FixtureIssuer(),
            confirm_synthetic=synthetic,
            confirm_production=confirmed,
        )
    ConversationWorker(object(), FixtureIssuer(), confirm_production=True)  # type: ignore[arg-type]


def test_the_issuer_client_uses_the_compose_name_only_in_production(tmp_path: Path) -> None:
    import datetime

    from cryptography import x509  # type: ignore[import-not-found]
    from cryptography.hazmat.primitives import (  # type: ignore[import-not-found]
        hashes,
        serialization,
    )
    from cryptography.hazmat.primitives.asymmetric import ec  # type: ignore[import-not-found]
    from cryptography.x509.oid import NameOID  # type: ignore[import-not-found]

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test CA")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    ca = tmp_path / "ca.pem"
    ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    client = RemoteFixtureIssuer("https://issuer:19444/fixture", ca, TOKEN, production=True)
    assert (client.host, client.port) == ("issuer", 19444)
    with pytest.raises(IdentityUnavailable):
        RemoteFixtureIssuer("https://issuer:19444/fixture", ca, TOKEN)
    for endpoint in [
        "https://127.0.0.1:19444/fixture",
        "https://issuer.example.com:19444/fixture",
        "https://issuer:19444/other",
    ]:
        with pytest.raises(IdentityUnavailable):
            RemoteFixtureIssuer(endpoint, Path("/absent"), TOKEN, production=True)


def test_the_issuer_needs_google_without_a_seed_file() -> None:
    with pytest.raises(IdentityUnavailable):
        StoredIssuer(object(), object(), None)  # type: ignore[arg-type]
    issuer = StoredIssuer(object(), object(), None, google=object())  # type: ignore[arg-type]
    assert issuer._accounts == {}


def test_production_uses_real_google_with_system_trust() -> None:
    service = ControlService(production(), {})
    trust, endpoints, keys_url = service.google()
    assert isinstance(trust, ssl.SSLContext) and trust.verify_mode == ssl.CERT_REQUIRED
    assert endpoints == GOOGLE and keys_url == GOOGLE_KEYS_URL


def test_production_refuses_a_local_database() -> None:
    good = f"host={HOST} dbname=postgres user=g sslmode=verify-full sslrootcert={CA}"
    service = ControlService(production(), {"MARKOS_CONTROL_DATABASE_DSN": good})
    assert service.database("MARKOS_CONTROL_DATABASE_DSN") == good
    local = "host=127.0.0.1 dbname=markos_test_x user=g"
    service = ControlService(production(), {"MARKOS_CONTROL_DATABASE_DSN": local})
    with pytest.raises(FatalServiceError):
        service.database("MARKOS_CONTROL_DATABASE_DSN")


def test_production_bills_go_to_spaces() -> None:
    secrets = {
        "MARKOS_CONTROL_SPACES_ACCESS_KEY": "access",
        "MARKOS_CONTROL_SPACES_SECRET_KEY": "secret",
    }

    async def build(environ: dict[str, str]) -> Any:
        async with AsyncExitStack() as stack:
            return ControlService(production(), environ).bill_store(stack)

    store = asyncio.run(build(secrets))
    assert isinstance(store, SpacesObjects) and store.bucket == "markos"
    with pytest.raises(FatalServiceError):
        asyncio.run(build({}))

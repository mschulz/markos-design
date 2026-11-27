"""The owner's provisioning steps never overwrite, never print a secret and fail closed."""

import json
import socket
import stat
import threading
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import pytest

from markos_runtime import provision
from markos_runtime.contracts.base import ContractError
from markos_runtime.control_lifecycle.production import SpacesSettings

SITE = provision.Site(
    schema_version=1,
    domain="markos.example.com",
    owner_id=UUID("11111111-1111-4111-8111-111111111111"),
    database_host="db.abcdefghijklmnop.supabase.co",
    google_client_id="123456789-abc.apps.googleusercontent.com",
    google_subject="100000000000000000001",
    markos_subject="owner",
    spaces=SpacesSettings(
        endpoint="https://syd1.digitaloceanspaces.com", region="syd1", bucket="markos-bills"
    ),
)


@pytest.fixture
def etc(tmp_path: Path) -> Path:
    provision.folders(tmp_path)
    (tmp_path / "site.json").write_bytes(SITE.to_wire())
    return tmp_path


def test_site_rejects_bad_values(etc: Path) -> None:
    body = json.loads(SITE.to_wire())
    for field, value in [
        ("domain", "https://markos.example.com"),
        ("database_host", "127.0.0.1"),
        ("owner_id", "not-a-uuid"),
        ("extra", "x"),
    ]:
        (etc / "site.json").write_text(json.dumps({**body, field: value}))
        with pytest.raises(ContractError):
            provision.load_site(etc)


def test_generated_secrets_are_private_and_never_rotated(etc: Path) -> None:
    made = provision.generate(etc, SITE)
    assert "session-key" in made and "migrator-database-dsn" in made
    assert not provision.OWNER_SECRETS & set(made)
    before = {p.name: p.read_bytes() for p in (etc / "secrets").iterdir()}
    assert provision.generate(etc, SITE) == []
    assert {p.name: p.read_bytes() for p in (etc / "secrets").iterdir()} == before
    assert stat.S_IMODE((etc / "secrets").stat().st_mode) == 0o700
    for path in (etc / "secrets").iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o400


def test_service_secrets_pass_the_production_checks(etc: Path) -> None:
    from markos_runtime.control_lifecycle.production import production_dsn

    provision.generate(etc, SITE)
    for name in provision.LOGINS:
        dsn = provision.read_secret(etc, name + "-database-dsn")
        production_dsn(dsn, SITE.database_host, "/etc/markos/tls/supabase-ca.pem")


def test_put_secret_refuses_to_overwrite(etc: Path) -> None:
    assert provision.put_secret(etc, "google-client-secret", "first")
    assert not provision.put_secret(etc, "google-client-secret", "second")
    assert provision.read_secret(etc, "google-client-secret") == "first"


def test_definitions_match_the_broker_secrets(etc: Path) -> None:
    provision.generate(etc, SITE)
    body = provision.definitions(etc)
    users = {u["name"] for u in body["users"]}
    assert users == {"chatpub", "chatworker", "chatactivity", "observer"}
    assert all("password" not in u for u in body["users"])
    for user, (dsn, _, _) in provision.BROKER.items():
        url = urlsplit(provision.read_secret(etc, dsn))
        assert (url.scheme, url.hostname, url.port, url.path) == (
            "amqps",
            "rabbitmq",
            5671,
            "/" + provision.VHOST,
        )
        assert url.username == user
    observer = [p for p in body["permissions"] if p["user"] == "observer"]
    assert observer == [
        {"user": "observer", "vhost": "markos", "configure": "^$", "write": "^$", "read": "^$"}
    ]


def test_configs_are_valid_production_profiles(etc: Path) -> None:
    from markos_runtime.control_lifecycle.production import load_production

    assert set(provision.configs(etc, SITE)) == set(provision.SERVICES)
    assert provision.configs(etc, SITE) == []
    for name in provision.SERVICES:
        cfg = load_production(etc / "config" / (name + ".json"))
        assert cfg.api_origin == "https://markos.example.com"
        assert cfg.database_host == SITE.database_host
        assert cfg.mail_enabled is False
    issuer = load_production(etc / "config" / "issuer.json")
    assert issuer.google_signin_subjects == {"100000000000000000001": "owner"}


def test_database_step_requires_a_verified_admin_dsn(
    etc: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    provision.generate(etc, SITE)
    provision.put_secret(etc, "supabase-admin-dsn", "host=db.abcdefghijklmnop.supabase.co")
    assert provision.main(["--etc", str(etc), "database"]) == 2
    assert "verify-full" in capsys.readouterr().err


def test_output_never_contains_a_secret(etc: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert provision.main(["--etc", str(etc), "secrets"]) == 0
    assert provision.main(["--etc", str(etc), "definitions"]) == 0
    assert provision.main(["--etc", str(etc), "configs"]) == 0
    out = capsys.readouterr()
    for path in (etc / "secrets").iterdir():
        value = path.read_text().strip()
        assert value not in out.out and value not in out.err


def test_tls_makes_a_strict_server_certificate(etc: Path) -> None:
    pytest.importorskip("cryptography")
    import ssl

    provision.tls(etc)
    ca = (etc / "tls" / "ca.pem").read_bytes()
    provision.tls(etc)
    assert (etc / "tls" / "ca.pem").read_bytes() == ca
    client = ssl.create_default_context(cafile=str(etc / "tls" / "ca.pem"))
    assert client.verify_flags & ssl.VERIFY_X509_STRICT
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server.load_cert_chain(etc / "tls" / "server.pem", etc / "secrets" / "tls-server-key")
    assert stat.S_IMODE((etc / "secrets" / "tls-server-key").stat().st_mode) == 0o400
    left, right = socket.socketpair()
    with left, right:
        accept = threading.Thread(
            target=lambda: server.wrap_socket(right, server_side=True).close()
        )
        accept.start()
        # Each Compose name in the certificate verifies under Python's strict checks.
        with client.wrap_socket(left, server_hostname="rabbitmq") as tls:
            assert tls.version() in {"TLSv1.2", "TLSv1.3"}
        accept.join(5)

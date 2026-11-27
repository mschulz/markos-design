"""The production Compose file boots with every fixture off, behind Caddy only."""

import http.client
import json
import secrets
import socket
import ssl
from typing import Any
from urllib.parse import urlencode, urlsplit

import pytest

from tests.production_boot.support import DOMAIN, SERVICES, command, compose, port

PUBLISHED = {"caddy", "supabase"}


def containers() -> dict[str, dict[str, Any]]:
    ids = compose("ps", "-a", "-q").decode().split()
    found = json.loads(command(["docker", "inspect", *ids]))
    return {c["Config"]["Labels"]["com.docker.compose.service"]: c for c in found}


@pytest.fixture(scope="module")
def caddy_trust() -> ssl.SSLContext:
    root = compose("exec", "-T", "caddy", "cat", "/data/caddy/pki/authorities/local/root.crt")
    return ssl.create_default_context(cadata=root.decode())


def get(
    trust: ssl.SSLContext, method: str, path: str, body: bytes | None = None
) -> http.client.HTTPResponse:
    """One request to the public origin, sent to Caddy's loopback port."""
    raw = socket.create_connection(("127.0.0.1", port("MARKOS_CI_HTTPS_PORT")), timeout=10)
    conn = http.client.HTTPConnection(DOMAIN, timeout=10)
    conn.sock = trust.wrap_socket(raw, server_hostname=DOMAIN)
    headers = {"Origin": "https://" + DOMAIN}
    if body is not None:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=body, headers=headers)
    return conn.getresponse()


def test_every_service_is_healthy_without_restarts() -> None:
    found = containers()
    assert set(SERVICES) | {"caddy", "rabbitmq", "supabase"} == set(found)
    for name, item in found.items():
        assert item["State"]["Health"]["Status"] == "healthy", name
        assert item["RestartCount"] == 0, name


def test_only_caddy_listens_on_the_host() -> None:
    for name, item in containers().items():
        bound = [b for b in (item["NetworkSettings"]["Ports"] or {}).values() if b]
        if name in PUBLISHED:
            assert all(x["HostIp"] == "127.0.0.1" for b in bound for x in b), name
        else:
            assert not bound, name


def test_services_run_the_production_command_without_fixture_mounts() -> None:
    for name, item in containers().items():
        if name not in SERVICES:
            continue
        assert item["Config"]["Cmd"][:2] == ["serve-control-production", "--confirm-production"]
        assert item["Config"]["User"] == "10001:10001"
        assert item["HostConfig"]["ReadonlyRootfs"] is True
        assert item["HostConfig"]["CapDrop"] == ["ALL"]
        targets = {m["Destination"] for m in item["Mounts"]}
        assert not any(t.startswith("/fixture") for t in targets), name


def test_caddy_serves_the_gateway_over_https(caddy_trust: ssl.SSLContext) -> None:
    response = get(caddy_trust, "GET", "/live")
    assert response.status == 200
    assert json.loads(response.read())["live"] is True
    assert response.getheader("Strict-Transport-Security") == "max-age=31536000"
    assert response.getheader("Server") is None


def test_sign_in_goes_to_google(caddy_trust: ssl.SSLContext) -> None:
    query = {k: secrets.token_urlsafe(32) for k in ("state", "nonce", "code_challenge")}
    response = get(caddy_trust, "GET", "/app/authorize?" + urlencode({**query, "mode": "cli"}))
    assert response.status == 303
    target = urlsplit(response.getheader("Location", ""))
    assert (target.scheme, target.netloc) == ("https", "accounts.google.com")


def test_fixture_login_is_absent(caddy_trust: ssl.SSLContext) -> None:
    body = json.dumps({"request": "x", "proof": "y"}).encode()
    assert get(caddy_trust, "POST", "/auth/fixture/authorize", body).status == 404

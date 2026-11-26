"""Real localhost TLS/HTTP fixture; no remote provider, proxy, deployment or real account."""

import asyncio
import http.client
import ipaddress
import json
import socket
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import uvicorn  # type: ignore[import-not-found]
from cryptography import x509  # type: ignore[import-not-found]
from cryptography.hazmat.primitives import hashes, serialization  # type: ignore[import-not-found]
from cryptography.hazmat.primitives.asymmetric import ec  # type: ignore[import-not-found]
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID  # type: ignore[import-not-found]

from markos_runtime.api.application import create_app
from markos_runtime.api.identity import FixtureIssuer, opaque
from markos_runtime.control.api_config import SyntheticApiConfig
from markos_runtime.control.store import ControlStore
from tests.control_api.test_api import SCOPE, Client, agent


def certificate(directory: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MarkOS localhost fixture")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    certfile, keyfile = directory / "fixture.pem", directory / "fixture-key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    keyfile.chmod(0o600)
    return certfile, keyfile


class HttpClient(Client):
    def __init__(self, port: int, cert: Path) -> None:
        super().__init__(None, "https://127.0.0.1:" + str(port))
        self.port = port
        self.tls = ssl.create_default_context(cafile=str(cert))

    async def call(
        self,
        method: str,
        path: str,
        value: Any = None,
        *,
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
        scheme: str = "https",
        extra: list[tuple[bytes, bytes]] | None = None,
    ) -> tuple[int, Any]:
        assert scheme == "https" and extra is None
        selected = {"host": "127.0.0.1:" + str(self.port)}
        if method == "POST":
            selected.update({"content-type": "application/json", "origin": self.origin})
        if self.cookies:
            selected["cookie"] = "; ".join(k + "=" + v for k, v in self.cookies.items())
        if self.bearer:
            selected["authorization"] = "Bearer " + self.bearer
        if self.csrf:
            selected["x-csrf-token"] = self.csrf
        selected.update(headers or {})
        body = (
            raw if raw is not None else (json.dumps(value).encode() if value is not None else None)
        )

        def exchange() -> tuple[int, list[tuple[str, str]], bytes]:
            connection = http.client.HTTPSConnection(
                "127.0.0.1", self.port, context=self.tls, timeout=5
            )
            try:
                connection.request(method, path, body=body, headers=selected)
                response = connection.getresponse()
                return response.status, response.getheaders(), response.read(262145)
            finally:
                connection.close()

        status, response_headers, output = await asyncio.to_thread(exchange)
        self.last_headers = [(k.lower().encode(), v.encode()) for k, v in response_headers]
        from http.cookies import SimpleCookie

        for k, v in self.last_headers:
            if k == b"set-cookie":
                parsed = SimpleCookie()
                parsed.load(v.decode())
                for name, item in parsed.items():
                    if item["max-age"] == "0":
                        self.cookies.pop(name, None)
                    else:
                        self.cookies[name] = item.value
        try:
            result = json.loads(output)
        except ValueError:
            result = output.decode()
        return status, result


def test_actual_https_browser_cli_current_permissions_and_stale_writes(
    control: dict[str, Any], tmp_path: Path
) -> None:
    cert, key = certificate(tmp_path)

    async def scenario() -> None:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(32)
        port = listener.getsockname()[1]
        issuer = FixtureIssuer()
        credential = opaque()
        issuer.add_account("both", credential)
        config = SyntheticApiConfig(schema_version=1, api_origin="https://127.0.0.1:" + str(port))
        async with ControlStore.connect(control["gateway"]) as store:
            app = create_app(config, store, issuer, session_key=b"s" * 32, confirm_synthetic=True)
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    ssl_certfile=str(cert),
                    ssl_keyfile=str(key),
                    proxy_headers=False,
                    access_log=False,
                    log_config=None,
                    lifespan="off",
                    timeout_graceful_shutdown=2,
                    limit_concurrency=16,
                    h11_max_incomplete_event_size=16384,
                )
            )
            task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                async with asyncio.timeout(5):
                    while not server.started:
                        await asyncio.sleep(0.01)
                browser = HttpClient(port, cert)
                assert (await browser.call("GET", "/live")) == (200, {"live": True})
                assert (await browser.call("GET", "/ready")) == (200, {"ready": True})
                assert (await browser.call("GET", "/api/v1/me"))[0] == 401
                await browser.login(issuer, credential)
                assert (await browser.call("POST", "/api/v1/agents", agent()))[0] == 201
                cli = HttpClient(port, cert)
                await cli.login(issuer, credential, cli=True)
                assert (await cli.call("GET", "/api/v1/agents" + SCOPE))[1] == (
                    await browser.call("GET", "/api/v1/agents" + SCOPE)
                )[1]
                assert (await cli.call("POST", "/api/v1/agents", agent()))[0] == 409
                with psycopg.connect(control["migration"], autocommit=True) as conn:
                    conn.execute(
                        "DELETE FROM markos_control.memberships "
                        "WHERE principal_id=%s AND domain='personal'",
                        (control["principals"]["both"],),
                    )
                assert (await cli.call("GET", "/api/v1/agents" + SCOPE))[0] == 403
                assert (await browser.call("GET", "/api/v1/agents" + SCOPE))[0] == 403
                browser.csrf = None
                assert (await browser.call("POST", "/api/v1/agents", agent()))[0] == 403
                assert (await cli.call("POST", "/api/v1/session/logout", {"schema_version": 1}))[
                    0
                ] == 200
                assert (await cli.call("GET", "/api/v1/me"))[0] == 401
            finally:
                server.should_exit = True
                await asyncio.wait_for(task, 5)
                listener.close()

    asyncio.run(scenario())

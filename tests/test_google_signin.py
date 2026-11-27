"""Google sign-in client against the fixture Google app, in process. No request leaves it."""

import asyncio
from collections.abc import Coroutine
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

httpx2 = pytest.importorskip("httpx2")
jwt = pytest.importorskip("jwt")
pytest.importorskip("fastapi")
pytest.importorskip("cryptography")

from markos_runtime.api.identity import IdentityDenied, challenge  # noqa: E402
from markos_runtime.control_lifecycle.google_fixture import SUBJECT, google_app  # noqa: E402
from markos_runtime.control_lifecycle.google_signin import GoogleSignIn  # noqa: E402
from markos_runtime.mail.google import GoogleEndpoints, OAuthClient  # noqa: E402

ORIGIN = "https://127.0.0.1:19445"
REDIRECT = "https://127.0.0.1:18443/auth/google/callback"
OAUTH = OAuthClient("markos-fixture.apps.googleusercontent.test", "fixture-client-secret")
ADMIN = "fixture-admin-token"
VERIFIER = "v" * 43


def run[T](work: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(work)


def fixture() -> Any:
    app = google_app(
        ORIGIN,
        OAUTH.client_id,
        OAUTH.client_secret,
        "https://127.0.0.1:18443/api/v1/mail/callback",
        "https://127.0.0.1:18443/api/v1/mail/alerts",
        "gmail-push@markos-fixture.iam.gserviceaccount.test",
        ADMIN,
        signin_redirect_uri=REDIRECT,
    )
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=ORIGIN)


class FixtureKeys:
    """The fixture's published keys, read through the in-process client."""

    def __init__(self, http: Any) -> None:
        self.http = http

    async def key(self, kid: str) -> Any:
        keys = (await self.http.get("/oauth2/v3/certs")).json()["keys"]
        match = [k for k in keys if k["kid"] == kid]
        if not match:
            raise jwt.PyJWKClientError("unknown kid")
        return jwt.PyJWK(match[0]).key


class OtherKeys:
    """A key Google never published: every ID token fails its signature check."""

    async def key(self, kid: str) -> Any:
        from cryptography.hazmat.primitives.asymmetric.rsa import (  # type: ignore[import-not-found,unused-ignore]
            generate_private_key,
        )

        return generate_private_key(public_exponent=65537, key_size=2048).public_key()


def signin(http: Any, keys: Any = None, subjects: dict[str, str] | None = None) -> GoogleSignIn:
    return GoogleSignIn(
        OAUTH,
        http,
        GoogleEndpoints.at(ORIGIN),
        keys or FixtureKeys(http),
        REDIRECT,
        subjects or {SUBJECT: "both"},
    )


async def browser(http: Any, client: GoogleSignIn, nonce: str = "nonce-1") -> str:
    """The browser's trip through the fixture sign-in screen. Returns Google's code."""
    url = client.authorization_url("state-1", challenge(VERIFIER), nonce)
    query = parse_qs(urlsplit(url).query)
    assert query["scope"] == ["openid email"] and query["nonce"] == [nonce]
    response = await http.get(url)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(REDIRECT + "?")
    back = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
    assert back["state"] == "state-1"
    return str(back["code"])


def test_sign_in_maps_the_google_subject_and_returns_the_nonce() -> None:
    async def scenario() -> None:
        async with fixture() as http:
            client = signin(http)
            code = await browser(http, client)
            assert await client.sign_in(code, VERIFIER) == ("both", "nonce-1")
            # Google's code works once.
            with pytest.raises(IdentityDenied):
                await client.sign_in(code, VERIFIER)

    run(scenario())


def test_sign_in_refuses_unmapped_subject_bad_verifier_and_foreign_key() -> None:
    async def scenario() -> None:
        async with fixture() as http:
            client = signin(http)
            with pytest.raises(IdentityDenied):
                await client.sign_in(await browser(http, client), "w" * 43)
            with pytest.raises(IdentityDenied):
                await signin(http, OtherKeys()).sign_in(await browser(http, client), VERIFIER)
            response = await http.post(
                "/fixture-control",
                json={"operation": "subject", "subject": "100000000000000000002"},
                headers={"Authorization": "Bearer " + ADMIN},
            )
            assert response.status_code == 200
            with pytest.raises(IdentityDenied):
                await client.sign_in(await browser(http, client), VERIFIER)
            with pytest.raises(IdentityDenied):
                await client.sign_in("not a code!", VERIFIER)

    run(scenario())


def test_fixture_refuses_sign_in_without_nonce_or_on_the_mail_redirect() -> None:
    async def scenario() -> None:
        async with fixture() as http:
            url = signin(http).authorization_url("s", challenge(VERIFIER), "n")
            no_nonce = url.replace("&nonce=n", "")
            assert (await http.get(no_nonce)).status_code == 400
            mail = url.replace("auth%2Fgoogle%2Fcallback", "api%2Fv1%2Fmail%2Fcallback")
            assert (await http.get(mail)).status_code == 400

    run(scenario())


def test_subject_map_is_explicit() -> None:
    with pytest.raises(ValueError):
        GoogleSignIn(OAUTH, None, GoogleEndpoints.at(ORIGIN), OtherKeys(), REDIRECT, {})
    with pytest.raises(ValueError):
        GoogleSignIn(
            OAUTH, None, GoogleEndpoints.at(ORIGIN), OtherKeys(), REDIRECT, {"a@b.c": "both"}
        )


@pytest.mark.parametrize(
    ("component", "service", "fields", "ok"),
    [
        ("gateway", "control-gateway", {"google_signin": True}, True),
        ("issuer", "control-gateway", {"google_signin_subjects": {SUBJECT: "both"}}, True),
        ("issuer", "control-gateway", {"google_signin": True}, False),
        ("gateway", "control-gateway", {"google_signin_subjects": {SUBJECT: "both"}}, False),
        ("issuer", "control-gateway", {"google_signin_subjects": {}}, False),
        ("issuer", "control-gateway", {"google_signin_subjects": {"a@b.c": "both"}}, False),
    ],
)
def test_sign_in_gate_is_per_component(
    component: str, service: str, fields: dict[str, Any], ok: bool
) -> None:
    import json

    from markos_runtime.control_lifecycle.config import ControlServiceConfig

    values = {
        "schema_version": 1,
        "profile": "control-process-fixture-v1",
        "component": component,
        "service_id": service,
        "owner_id": "11111111-1111-4111-8111-111111111111",
        "domain": "personal",
        "health_port": 8781,
        "ca_file": "/etc/test-ca/ca.pem",
        "certificate_file": "/etc/test-ca/server.pem",
        "private_key_file": "/etc/test-ca/server.key",
        "fixture_state_file": "/fixture/state.json",
        "fixture_seed_file": "/fixture/seed.json",
        "broker_publisher_user": "synthetic-publisher",
        **fields,
    }
    raw = json.dumps(values).encode()
    if ok:
        ControlServiceConfig.from_wire(raw)
    else:
        with pytest.raises(ValueError):
            ControlServiceConfig.from_wire(raw)

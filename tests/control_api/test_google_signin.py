"""Google sign-in through the issuer and the gateway, on disposable PostgreSQL.

The issuer test runs the real GoogleSignIn against the in-process fixture Google. The gateway
tests use an issuer double, so they check only the gateway's redirect, cookie and code handling.
"""

import asyncio
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2  # type: ignore[import-not-found,unused-ignore]
import jwt  # type: ignore[import-not-found]
import pytest

from markos_runtime.api.application import COOKIE, create_app
from markos_runtime.api.identity import (
    FixtureIssuer,
    IdentityDenied,
    SecretCodec,
    challenge,
    opaque,
)
from markos_runtime.control.api_config import SyntheticApiConfig, SyntheticWebApiConfig
from markos_runtime.control_lifecycle.google_fixture import SUBJECT, google_app
from markos_runtime.control_lifecycle.issuer import IssuerStore, StoredIssuer, save_private
from tests.control_api.test_api import ORIGIN, Client
from tests.control_store_support import HarnessStore
from tests.test_google_signin import ADMIN, OAUTH, REDIRECT, FixtureKeys, signin

GOOGLE = "https://127.0.0.1:19445"


def test_issuer_google_sign_in_mints_a_code_for_the_gateway_flow(
    tmp_path: Path, control: dict[str, Any]
) -> None:
    tmp_path.chmod(0o700)
    seed = tmp_path / "seed.json"
    save_private(seed, {"schema_version": 1, "accounts": []})
    app = google_app(
        GOOGLE,
        OAUTH.client_id,
        OAUTH.client_secret,
        ORIGIN + "/api/v1/mail/callback",
        ORIGIN + "/api/v1/mail/alerts",
        "gmail-push@markos-fixture.iam.gserviceaccount.test",
        ADMIN,
        signin_redirect_uri=REDIRECT,
    )

    async def google_trip(http: Any, url: str) -> dict[str, str]:
        response = await http.get(url)
        assert response.status_code == 303
        return {k: v[0] for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()}

    async def scenario() -> None:
        transport = httpx2.ASGITransport(app=app)
        async with (
            httpx2.AsyncClient(transport=transport, base_url=GOOGLE) as http,
            IssuerStore.connect(control["issuer"]) as store,
        ):
            issuer = StoredIssuer(
                store, SecretCodec(os.urandom(32)), seed, google=signin(http, FixtureKeys(http))
            )
            await issuer.start()
            verifier, nonce = opaque(), opaque()
            started = await issuer.google_begin(challenge(verifier), nonce)
            back = await google_trip(http, started["url"])
            assert back["state"] == started["state"]
            code = await issuer.google_finish(back["state"], back["code"])
            token, _ = await issuer.exchange(code, verifier)
            claims = jwt.decode(token, options={"verify_signature": False})
            assert claims["sub"] == "both" and claims["nonce"] == nonce
            # The pending sign-in is used once.
            with pytest.raises(IdentityDenied):
                await issuer.google_finish(back["state"], back["code"])
            # An unmapped Google account is refused, and its pending row is gone.
            await http.post(
                "/fixture-control",
                json={"operation": "subject", "subject": SUBJECT + "9"},
                headers={"Authorization": "Bearer " + ADMIN},
            )
            started = await issuer.google_begin(challenge(verifier), nonce)
            back = await google_trip(http, started["url"])
            with pytest.raises(IdentityDenied):
                await issuer.google_finish(back["state"], back["code"])
            with pytest.raises(IdentityDenied):
                await issuer.google_finish(back["state"], back["code"])
            # Without the adapter, the issuer refuses the Google operations.
            plain = StoredIssuer(store, SecretCodec(os.urandom(32)), seed)
            with pytest.raises(IdentityDenied):
                await plain.google_begin(challenge(verifier), nonce)

    asyncio.run(scenario())


class GoogleDouble(FixtureIssuer):
    """Stands in for the issuer's Google steps. Google's code "4/good" signs in as `both`."""

    def __init__(self, credential: str) -> None:
        super().__init__()
        self.credential = credential
        self.pending: dict[str, tuple[str, str]] = {}

    async def google_begin(self, pkce_challenge: str, nonce: str) -> dict[str, str]:
        state = opaque()
        self.pending[state] = (pkce_challenge, nonce)
        url = GOOGLE + "/o/oauth2/v2/auth?" + urlencode({"state": state})
        return {"url": url, "state": state}

    async def google_finish(self, state: str, code: str) -> str:
        bound = self.pending.pop(state, None)
        if bound is None or code != "4/good":
            raise IdentityDenied("Google sign-in denied")
        return self.authorize(self.credential, *bound)


def web_config() -> SyntheticWebApiConfig:
    return SyntheticWebApiConfig(
        schema_version=1,
        api_origin=ORIGIN,
        conversation_profile="deterministic-conversation-v1",
        web_profile="synthetic-web-cli-v1",
    )


async def to_google(c: Client, mode: str, pkce: str | None = None) -> tuple[dict[str, Any], str]:
    """Starts a sign-in and follows /app/authorize. Returns the flow and Google's state."""
    if mode == "browser":
        status, flow = await c.call("POST", "/auth/login", {"schema_version": 1})
    else:
        status, flow = await c.call(
            "POST", "/auth/cli/start", {"schema_version": 1, "challenge": pkce}
        )
    assert status == 200, flow
    query = parse_qs(urlsplit(flow["authorization_url"]).query)
    params = {k: query[k][0] for k in ("state", "nonce", "code_challenge")}
    status, _ = await c.call("GET", "/app/authorize?" + urlencode({**params, "mode": mode}))
    assert status == 303
    location = dict(c.last_headers)[b"location"].decode()
    assert location.startswith(GOOGLE + "/o/oauth2/v2/auth?")
    assert "__Host-markos-google" in c.cookies
    return flow, parse_qs(urlsplit(location).query)["state"][0]


def callback(state: str, code: str = "4/good", **extra: str) -> str:
    # Google adds scope, authuser and prompt. The gateway accepts and ignores them.
    query = {"state": state, "code": code, "scope": "email openid", "authuser": "0", **extra}
    return "/auth/google/callback?" + urlencode(query)


def test_gateway_browser_and_cli_sign_in_through_google(control: dict[str, Any]) -> None:
    async def scenario() -> None:
        credential = opaque()
        issuer = GoogleDouble(credential)
        issuer.add_account("both", credential)
        async with HarnessStore.connect(control["gateway"]) as store:
            app = create_app(
                web_config(),
                store,
                issuer,
                session_key=b"s" * 32,
                confirm_synthetic=True,
                google_signin=True,
            )
            browser = Client(app)
            _, state = await to_google(browser, "browser")
            status, _ = await browser.call("GET", callback(state))
            assert status == 303 and "__Host-markos-google" not in browser.cookies
            location = dict(browser.last_headers)[b"location"].decode()
            assert location.startswith("/auth/callback?")
            assert (await browser.call("GET", location))[0] == 303
            assert COOKIE in browser.cookies
            assert (await browser.call("GET", "/api/v1/me"))[0] == 200
            # The fixture code form is off.
            status, _ = await browser.call(
                "POST", "/auth/fixture/authorize", {"request": "", "proof": "", "credential": ""}
            )
            assert status == 404

            cli = Client(app)
            verifier = opaque()
            flow, state = await to_google(cli, "cli", challenge(verifier))
            status, page = await cli.call("GET", callback(state))
            assert status == 200 and "one-use-code" in page
            code = page.split('id="one-use-code">')[1].split("<")[0]
            status, result = await cli.call(
                "POST",
                "/auth/cli/exchange",
                {
                    "schema_version": 1,
                    "state": flow["state"],
                    "binding": flow["binding"],
                    "code": code,
                    "verifier": verifier,
                },
            )
            assert status == 200, result

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["no-cookie", "other-state", "error", "refused", "unknown"])
def test_gateway_refuses_unbound_or_failed_google_callbacks(
    control: dict[str, Any], fault: str
) -> None:
    async def scenario() -> None:
        credential = opaque()
        issuer = GoogleDouble(credential)
        issuer.add_account("both", credential)
        async with HarnessStore.connect(control["gateway"]) as store:
            app = create_app(
                web_config(),
                store,
                issuer,
                session_key=b"s" * 32,
                confirm_synthetic=True,
                google_signin=True,
            )
            c = Client(app)
            _, state = await to_google(c, "browser")
            path = {
                "no-cookie": callback(state),
                "other-state": callback(opaque()),
                "error": callback(state, error="access_denied"),
                "refused": callback(state, code="4/bad"),
                "unknown": callback(state, extra="1"),
            }[fault]
            if fault == "no-cookie":
                c.cookies.pop("__Host-markos-google")
            status, _ = await c.call("GET", path)
            assert status == (400 if fault == "unknown" else 401)
            assert COOKIE not in c.cookies

    asyncio.run(scenario())


def test_google_route_is_off_by_default_and_needs_a_capable_issuer(
    control: dict[str, Any],
) -> None:
    async def scenario() -> None:
        async with HarnessStore.connect(control["gateway"]) as store:
            app = create_app(
                web_config(), store, FixtureIssuer(), session_key=b"s" * 32, confirm_synthetic=True
            )
            assert (await Client(app).call("GET", "/auth/google/callback"))[0] == 404
            with pytest.raises(ValueError):
                create_app(
                    web_config(),
                    store,
                    FixtureIssuer(),
                    session_key=b"s" * 32,
                    confirm_synthetic=True,
                    google_signin=True,
                )
            with pytest.raises(ValueError):
                create_app(
                    SyntheticApiConfig(schema_version=1, api_origin=ORIGIN),
                    store,
                    GoogleDouble(opaque()),
                    session_key=b"s" * 32,
                    confirm_synthetic=True,
                    google_signin=True,
                )

    asyncio.run(scenario())

"""Authentication boundary on disposable PostgreSQL; no real identity accounts."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

import jwt  # type: ignore[import-not-found]
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import ec  # type: ignore[import-not-found]

from markos_runtime.api.application import COOKIE, create_app
from markos_runtime.api.identity import (
    FixtureIssuer,
    IdentityDenied,
    IdentityUnavailable,
    IdentityVerifier,
    SecretCodec,
    challenge,
    opaque,
    strict_json,
)
from markos_runtime.control.api_config import SyntheticApiConfig
from markos_runtime.control.store import ControlStore
from tests.integration.conftest import OTHER, OWNER

ORIGIN = "https://127.0.0.1:18443"
SCOPE = "?owner_id=" + OWNER + "&domain=personal"


class Client:
    def __init__(self, app: Any, origin: str = ORIGIN) -> None:
        self.app, self.origin = app, origin
        self.cookies: dict[str, str] = {}
        self.bearer: str | None = None
        self.csrf: str | None = None
        self.last_headers: list[tuple[bytes, bytes]] = []

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
        url = urlsplit(path)
        body = (
            raw if raw is not None else (json.dumps(value).encode() if value is not None else b"")
        )
        selected = {"host": urlsplit(self.origin).netloc}
        if method == "POST":
            selected.update({"content-type": "application/json", "origin": self.origin})
        if self.cookies:
            selected["cookie"] = "; ".join(k + "=" + v for k, v in self.cookies.items())
        if self.bearer:
            selected["authorization"] = "Bearer " + self.bearer
        if self.csrf:
            selected["x-csrf-token"] = self.csrf
        selected.update(headers or {})
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": scheme,
            "path": url.path,
            "raw_path": url.path.encode(),
            "query_string": url.query.encode(),
            "headers": [(k.encode(), v.encode()) for k, v in selected.items()] + (extra or []),
            "client": ("127.0.0.1", 9999),
            "server": ("127.0.0.1", 18443),
            "root_path": "",
        }
        messages: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        await self.app(scope, receive, send)
        start = messages[0]
        self.last_headers = start["headers"]
        for k, v in self.last_headers:
            if k == b"set-cookie":
                parsed = SimpleCookie()
                parsed.load(v.decode())
                for name, item in parsed.items():
                    if item["max-age"] == "0":
                        self.cookies.pop(name, None)
                    else:
                        self.cookies[name] = item.value
        output = b"".join(m.get("body", b"") for m in messages)
        try:
            result = json.loads(output)
        except ValueError:
            result = output.decode()
        return int(start["status"]), result

    async def login(self, issuer: FixtureIssuer, credential: str, *, cli: bool = False) -> str:
        verifier = opaque()
        status, flow = await self.call(
            "POST",
            "/auth/cli/start" if cli else "/auth/login",
            {"schema_version": 1, **({"challenge": challenge(verifier)} if cli else {})},
        )
        assert status == 200, flow
        params = parse_qs(urlsplit(flow["authorization_url"]).query)
        code = issuer.authorize(credential, params["code_challenge"][0], params["nonce"][0])
        if cli:
            status, result = await self.call(
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
            self.bearer = result["access_token"]
            assert result["expires_in"] == 300
        else:
            status, result = await self.call(
                "GET", "/auth/callback?" + urlencode({"state": flow["state"], "code": code})
            )
            assert status == 200, result
            cookie_headers = str(self.last_headers).lower()
            assert (
                "secure" in cookie_headers
                and "httponly" in cookie_headers
                and "samesite=lax" in cookie_headers
            )
        status, me = await self.call("GET", "/api/v1/me")
        assert status == 200, me
        self.csrf = me.get("csrf_token")
        return self.bearer or self.cookies[COOKIE]


async def run_api(
    data: dict[str, Any],
    scenario: Callable[[Client, FixtureIssuer, dict[str, str], ControlStore], Awaitable[None]],
) -> None:
    issuer = FixtureIssuer()
    accounts = {name: opaque() for name in data["principals"]}
    for name, credential in accounts.items():
        issuer.add_account(name, credential)
    config = SyntheticApiConfig(schema_version=1, api_origin=ORIGIN)
    async with ControlStore.connect(data["gateway"]) as store:
        app = create_app(config, store, issuer, session_key=b"s" * 32, confirm_synthetic=True)
        await scenario(Client(app), issuer, accounts, store)


def agent() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "owner_id": OWNER,
        "domain": "personal",
        "agent_id": "control-test-agent",
        "revision": 1,
        "description": "<script>inert JSON only</script>",
    }


def test_browser_cli_registry_configuration_status_and_logout(control: dict[str, Any]) -> None:
    async def scenario(
        c: Client, issuer: FixtureIssuer, accounts: dict[str, str], store: ControlStore
    ) -> None:
        old = await c.login(issuer, accounts["both"])
        assert (await c.call("GET", "/api/v1/status" + SCOPE))[1] == {
            "state": "unknown",
            "services": [],
        }
        assert (await c.call("POST", "/api/v1/agents", agent()))[0] == 201
        assert (await c.call("POST", "/api/v1/agents", agent()))[0] == 409
        status, rows = await c.call("GET", "/api/v1/agents" + SCOPE)
        assert status == 200 and len(rows) == 1 and rows[0]["enabled"] is False
        assert "<script>" in rows[0]["manifest"]["description"]
        assert (
            await c.call(
                "POST",
                "/api/v1/agents/control-test-agent/state" + SCOPE,
                {"schema_version": 1, "expected_revision": 1, "enabled": True, "reason": "Fixture"},
            )
        )[0] == 200
        config = {
            "schema_version": 1,
            "owner_id": OWNER,
            "domain": "personal",
            "revision": 1,
            "previous_revision": 0,
            "agent": {**agent(), "revision": 2},
            "reason": "Fixture config",
        }
        assert (await c.call("POST", "/api/v1/configuration/revisions", config))[0] == 200
        cli = Client(c.app)
        await cli.login(issuer, accounts["both"], cli=True)
        assert (await cli.call("GET", "/api/v1/agents" + SCOPE))[1] == (
            await c.call("GET", "/api/v1/agents" + SCOPE)
        )[1]
        assert (await cli.call("GET", "/api/v1/configuration" + SCOPE))[0] == 200
        assert (await c.call("POST", "/api/v1/session/refresh", {"schema_version": 1}))[0] == 200
        current = await c.login(issuer, accounts["both"])
        assert current != old
        stale = Client(c.app)
        stale.cookies[COOKIE] = old
        assert (await stale.call("GET", "/api/v1/me"))[0] == 401
        assert (await c.call("POST", "/api/v1/session/logout", {"schema_version": 1}))[0] == 200
        c.cookies[COOKIE] = current
        assert (await c.call("GET", "/api/v1/me"))[0] == 401
        with psycopg.connect(control["migration"]) as conn:
            for table in ("events", "outbox", "tasks"):
                assert conn.execute("SELECT count(*) FROM markos_runtime." + table).fetchone() == (
                    0,
                )
            values = conn.execute(
                "SELECT access_cipher,refresh_cipher FROM markos_control.api_credentials"
            ).fetchall()
            assert values and all(not v.startswith("eyJ") for row in values for v in row)

    asyncio.run(run_api(control, scenario))


@pytest.mark.parametrize(
    "case",
    [
        "csrf",
        "origin",
        "host",
        "http",
        "duplicate",
        "cookie-duplicate",
        "ambiguous",
        "channel",
        "content",
        "body",
        "query",
        "query-duplicate",
        "forged",
        "role",
        "owner",
        "domain",
        "disabled",
        "upstream",
        "expiry",
        "idle",
        "generation",
        "logout-revoked",
        "unknown-route",
        "method",
    ],
)
def test_request_and_authority_rejections(control: dict[str, Any], case: str) -> None:
    async def scenario(
        c: Client, issuer: FixtureIssuer, accounts: dict[str, str], store: ControlStore
    ) -> None:
        token = await c.login(issuer, accounts["viewer"])
        method, path, value = "GET", "/api/v1/agents" + SCOPE, None
        options: dict[str, Any] = {}
        expected = 403
        if case == "csrf":
            method, path, value = "POST", "/api/v1/agents", agent()
            c.csrf = None
        elif case == "origin":
            options["headers"] = {"origin": "https://evil.test"}
        elif case == "host":
            options["headers"] = {"host": "evil.test"}
        elif case == "http":
            options["scheme"] = "http"
        elif case == "duplicate":
            options["extra"] = [(b"host", b"evil.test")]
            expected = 400
        elif case == "cookie-duplicate":
            options["headers"] = {"cookie": COOKIE + "=" + token + "; " + COOKIE + "=" + token}
            expected = 400
        elif case == "ambiguous":
            c.bearer = token
            expected = 400
        elif case == "channel":
            c.cookies = {}
            c.bearer = token
            expected = 401
        elif case == "content":
            method, path, value = "POST", "/api/v1/agents", agent()
            options["headers"] = {"content-type": "text/plain"}
            expected = 400
        elif case == "body":
            options["raw"] = b"x" * 65537
            expected = 413
        elif case == "query":
            path += "&role=administrator"
            expected = 400
        elif case == "query-duplicate":
            path += "&domain=work"
            expected = 400
        elif case == "forged":
            method, path, value = (
                "POST",
                "/api/v1/agents",
                {**agent(), "actor_id": str(control["principals"]["both"])},
            )
            expected = 400
        elif case == "role":
            method, path, value = "POST", "/api/v1/agents", agent()
        elif case == "owner":
            path = path.replace(OWNER, OTHER)
        elif case == "domain":
            path = path.replace("personal", "work")
        elif case == "unknown-route":
            path = "/api/v1/chat"
            expected = 404
        elif case == "method":
            method = "PUT"
            expected = 404
        else:
            sid = UUID(token.split(".")[0])
            with psycopg.connect(control["migration"], autocommit=True) as conn:
                if case == "disabled":
                    conn.execute(
                        "UPDATE markos_control.principals SET disabled=true WHERE subject='viewer'"
                    )
                    expected = 401
                elif case in {"upstream", "logout-revoked"}:
                    provider = conn.execute(
                        "SELECT provider_session_id FROM markos_control.sessions "
                        "WHERE session_id=%s",
                        (sid,),
                    ).fetchone()
                    assert provider
                    issuer.revoke(provider[0])
                    expected = 401
                    if case == "logout-revoked":
                        method, path, value = (
                            "POST",
                            "/api/v1/session/logout",
                            {"schema_version": 1},
                        )
                        expected = 200
                elif case == "expiry":
                    conn.execute(
                        "UPDATE markos_control.sessions SET created_at=clock_timestamp()-"
                        "interval '1 hour',provider_expires_at=clock_timestamp()-"
                        "interval '1 second' WHERE session_id=%s",
                        (sid,),
                    )
                    expected = 401
                elif case == "idle":
                    conn.execute(
                        "UPDATE markos_control.sessions SET last_seen_at=clock_timestamp()-"
                        "interval '31 minutes' WHERE session_id=%s",
                        (sid,),
                    )
                    expected = 401
                elif case == "generation":
                    conn.execute("UPDATE markos_runtime.runtime_epoch SET epoch=gen_random_uuid()")
                    expected = 401
        status, result = await c.call(method, path, value, **options)
        assert status == expected, (case, result)
        assert dict(c.last_headers)[b"cache-control"] == b"no-store"
        assert b"access-control-allow-origin" not in dict(c.last_headers)
        assert "synthetic-test-only" not in str(result) and "SELECT" not in str(result)

    asyncio.run(run_api(control, scenario))


def test_current_membership_and_shared_rate(control: dict[str, Any]) -> None:
    async def scenario(
        c: Client, issuer: FixtureIssuer, accounts: dict[str, str], store: ControlStore
    ) -> None:
        await c.login(issuer, accounts["both"])
        with psycopg.connect(control["migration"], autocommit=True) as conn:
            conn.execute(
                "DELETE FROM markos_control.memberships WHERE principal_id=%s "
                "AND domain='personal'",
                (control["principals"]["both"],),
            )
        assert (await c.call("GET", "/api/v1/agents" + SCOPE))[0] == 403
        assert (await c.call("GET", "/api/v1/me"))[1]["memberships"][0]["domain"] == "research"
        await c.login(issuer, accounts["administrator"])
        other = Client(
            create_app(
                SyntheticApiConfig(schema_version=1, api_origin=ORIGIN),
                store,
                issuer,
                session_key=b"s" * 32,
                confirm_synthetic=True,
            )
        )
        await other.login(issuer, accounts["administrator"], cli=True)
        statuses = []
        for index in range(6):
            statuses.append(
                (await (c if index % 2 else other).call("POST", "/api/v1/agents", agent()))[0]
            )
        assert statuses == [201, 409, 409, 409, 409, 429]

    asyncio.run(run_api(control, scenario))


@pytest.mark.parametrize("fault", ["state", "binding", "pkce", "code", "expired", "replay"])
def test_one_use_pkce_flow(control: dict[str, Any], fault: str) -> None:
    async def scenario(
        c: Client, issuer: FixtureIssuer, accounts: dict[str, str], store: ControlStore
    ) -> None:
        verifier = opaque()
        status, flow = await c.call(
            "POST", "/auth/cli/start", {"schema_version": 1, "challenge": challenge(verifier)}
        )
        assert status == 200
        params = parse_qs(urlsplit(flow["authorization_url"]).query)
        code = issuer.authorize(accounts["both"], params["code_challenge"][0], params["nonce"][0])
        value = {
            "schema_version": 1,
            "state": flow["state"],
            "binding": flow["binding"],
            "code": code,
            "verifier": verifier,
        }
        if fault == "expired":
            with psycopg.connect(control["migration"], autocommit=True) as conn:
                conn.execute(
                    "UPDATE markos_control.api_flows "
                    "SET expires_at=clock_timestamp()-interval '1 second'"
                )
        elif fault == "replay":
            assert (await c.call("POST", "/auth/cli/exchange", value))[0] == 200
        else:
            value[
                {"state": "state", "binding": "binding", "pkce": "verifier", "code": "code"}[fault]
            ] = opaque()
        assert (await c.call("POST", "/auth/cli/exchange", value))[0] == 401

    asyncio.run(run_api(control, scenario))


@pytest.mark.parametrize(
    "fault",
    [
        "iss",
        "aud",
        "sub",
        "sid",
        "nonce",
        "exp",
        "nbf",
        "iat",
        "kind",
        "extra",
        "signature",
        "algorithm",
        "header-url",
        "missing",
        "unknown-kid",
        "bool-time",
        "float-time",
    ],
)
def test_signed_token_rejection(control: dict[str, Any], fault: str) -> None:
    async def scenario(
        c: Client, issuer: FixtureIssuer, accounts: dict[str, str], store: ControlStore
    ) -> None:
        nonce, verifier = opaque(), opaque()
        code = issuer.authorize(accounts["both"], challenge(verifier), nonce)
        token, _ = await issuer.exchange(code, verifier)
        validator = IdentityVerifier(store, issuer)
        assert (await validator.verify(token, nonce)).subject == "both"
        claims = jwt.decode(token, options={"verify_signature": False})
        headers = {"kid": issuer.kid, "typ": "JWT"}
        algorithm = "ES256"
        if fault == "iss":
            claims["iss"] = "https://evil.test"
        elif fault == "aud":
            claims["aud"] = "other"
        elif fault == "sub":
            claims["sub"] = "administrator"
        elif fault == "sid":
            claims["sid"] = "00000000-0000-0000-0000-000000000000"
        elif fault == "nonce":
            claims["nonce"] = opaque()
        elif fault == "exp":
            claims["exp"] = claims["iat"] - 1
        elif fault == "nbf":
            claims["nbf"] += 1000
        elif fault == "iat":
            claims["iat"] += 1000
        elif fault == "kind":
            claims["token_kind"] = "service"
        elif fault == "extra":
            claims["role"] = "administrator"
        elif fault == "algorithm":
            algorithm = "ES384"
        elif fault == "header-url":
            headers["jku"] = "http://127.0.0.1/private"
        elif fault == "missing":
            del claims["sid"]
        elif fault == "unknown-kid":
            headers["kid"] = opaque()
        elif fault == "bool-time":
            claims["iat"] = True
        elif fault == "float-time":
            claims["iat"] = float(claims["iat"])
        key = FixtureIssuer()._key if fault == "signature" else issuer._key
        if fault == "algorithm":
            key = ec.generate_private_key(ec.SECP384R1())
        forged = jwt.encode(claims, key, algorithm=algorithm, headers=headers)
        with pytest.raises(IdentityDenied):
            await validator.verify(forged, nonce)

    asyncio.run(run_api(control, scenario))


def test_key_rotation_cache_and_auth_capacity(control: dict[str, Any]) -> None:
    async def scenario(
        c: Client, issuer: FixtureIssuer, accounts: dict[str, str], store: ControlStore
    ) -> None:
        verifier, nonce = opaque(), opaque()
        validator = IdentityVerifier(store, issuer)

        async def issue() -> str:
            return (
                await issuer.exchange(
                    issuer.authorize(accounts["both"], challenge(verifier), nonce), verifier
                )
            )[0]

        token = await issue()
        await validator.verify(token, nonce)
        issuer.rotate_key()
        rotated = await issue()
        with pytest.raises(IdentityDenied):
            await validator.verify(rotated, nonce)
        with psycopg.connect(control["migration"], autocommit=True) as conn:
            conn.execute(
                "UPDATE markos_control.api_key_cache "
                "SET next_refresh_at='-infinity',expires_at='-infinity'"
            )
        await validator.verify(rotated, nonce)
        with pytest.raises(IdentityDenied):
            await validator.verify(token, nonce)
        slots = [await store._call("api_auth_claim") for _ in range(4)]
        assert all(slots)
        with pytest.raises(IdentityUnavailable):
            await validator.provider_call("keys")
        for slot in slots:
            await store._call("api_auth_release", UUID(str(slot)))

    asyncio.run(run_api(control, scenario))


@pytest.mark.parametrize(
    "document",
    [
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b"[]",
        b"x" * 65537,
        b'{"keys":[]}',
        b'{"keys":[{"kty":"RSA","d":"private"}]}',
    ],
)
def test_strict_identity_documents(document: bytes) -> None:
    with pytest.raises(IdentityDenied):
        IdentityVerifier.validate_keys(document)


def test_secret_separation_and_explicit_factory() -> None:
    codec = SecretCodec(b"s" * 32)
    other = SecretCodec(b"t" * 32)
    assert codec.lookup("session", "a") != codec.lookup("csrf", "a")
    encrypted = codec.seal("synthetic-secret")
    assert codec.open(encrypted) == "synthetic-secret" and "synthetic-secret" not in encrypted
    with pytest.raises(IdentityDenied):
        other.open(encrypted)
    with pytest.raises(ValueError):
        SecretCodec(b"short")
    with pytest.raises(IdentityDenied):
        strict_json(b'{"x":Infinity}', 100)


def test_activity_cursor_is_bound_to_actor_and_scope(control: dict[str, Any]) -> None:
    from tests.integration.test_control_persistence import prepare, request, session

    async def scenario(
        c: Client, issuer: FixtureIssuer, accounts: dict[str, str], store: ControlStore
    ) -> None:
        ctx = await session(store)
        conversation = await prepare(store, ctx)
        await store.stage_message(ctx, UUID(OWNER), request(conversation))
        await c.login(issuer, accounts["both"])
        status, page = await c.call("GET", "/api/v1/activity" + SCOPE + "&limit=1")
        assert status == 200 and len(page["items"]) == 1 and page["next_cursor"]
        cursor = page["next_cursor"]
        assert (await c.call("GET", "/api/v1/activity" + SCOPE + "&cursor=" + cursor))[1][
            "items"
        ] == []
        assert (
            await c.call(
                "GET",
                "/api/v1/activity" + SCOPE.replace("personal", "research") + "&cursor=" + cursor,
            )
        )[0] == 400
        assert (await c.call("GET", "/api/v1/activity" + SCOPE + "&cursor=" + cursor[:-2] + "xx"))[
            0
        ] == 400
        cli = Client(c.app)
        await cli.login(issuer, accounts["viewer"], cli=True)
        assert (await cli.call("GET", "/api/v1/activity" + SCOPE + "&cursor=" + cursor))[0] == 400

    asyncio.run(run_api(control, scenario))


@pytest.mark.parametrize("state", ["ready", "degraded", "stale", "old-epoch"])
def test_scoped_status_freshness(control: dict[str, Any], state: str) -> None:
    async def scenario(
        c: Client, issuer: FixtureIssuer, accounts: dict[str, str], store: ControlStore
    ) -> None:
        with psycopg.connect(control["migration"], autocommit=True) as conn:
            conn.execute(
                "INSERT INTO markos_runtime.service_heartbeats "
                "SELECT %s,'personal','fixture-worker','worker',gen_random_uuid(),"
                "markos_runtime.current_epoch(),%s,clock_timestamp()",
                (OWNER, "degraded" if state == "degraded" else "ready"),
            )
            conn.execute(
                "INSERT INTO markos_runtime.service_heartbeats "
                "SELECT %s,'personal','private-other','worker',gen_random_uuid(),"
                "markos_runtime.current_epoch(),'ready',clock_timestamp()",
                (OTHER,),
            )
            if state == "stale":
                conn.execute(
                    "UPDATE markos_runtime.service_heartbeats "
                    "SET seen_at=clock_timestamp()-interval '46 seconds'"
                )
            if state == "old-epoch":
                conn.execute("UPDATE markos_runtime.service_heartbeats SET epoch=gen_random_uuid()")
        await c.login(issuer, accounts["viewer"])
        status, value = await c.call("GET", "/api/v1/status" + SCOPE)
        assert status == 200 and len(value["services"]) == 1
        assert value["services"][0]["state"] == (
            "fresh" if state == "ready" else "stale" if state == "old-epoch" else state
        )
        assert "private-other" not in str(value)

    asyncio.run(run_api(control, scenario))


def test_login_transaction_rollback_and_private_api_tables(control: dict[str, Any]) -> None:
    async def scenario(
        c: Client, issuer: FixtureIssuer, accounts: dict[str, str], store: ControlStore
    ) -> None:
        old = await c.login(issuer, accounts["both"])
        with psycopg.connect(control["migration"], autocommit=True) as conn:
            conn.execute(
                "CREATE FUNCTION markos_control.fail_test_credential() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic failure'; END $$"
            )
            conn.execute(
                "CREATE TRIGGER fail_test BEFORE INSERT ON markos_control.api_credentials "
                "FOR EACH ROW EXECUTE FUNCTION markos_control.fail_test_credential()"
            )
        status, flow = await c.call("POST", "/auth/login", {"schema_version": 1})
        assert status == 200
        params = parse_qs(urlsplit(flow["authorization_url"]).query)
        code = issuer.authorize(accounts["both"], params["code_challenge"][0], params["nonce"][0])
        callback = "/auth/callback?" + urlencode({"code": code, "state": flow["state"]})
        assert (await c.call("GET", callback))[0] == 503
        assert c.cookies[COOKIE] == old and (await c.call("GET", "/api/v1/me"))[0] == 200
        assert (await c.call("GET", callback))[0] == 401
        with psycopg.connect(control["migration"], autocommit=True) as conn:
            assert conn.execute("SELECT count(*) FROM markos_control.sessions").fetchone() == (1,)
            conn.execute("DROP TRIGGER fail_test ON markos_control.api_credentials")
        await c.login(issuer, accounts["both"])
        for identity in ("gateway", "publisher", "unmapped"):
            with (
                psycopg.connect(control[identity], autocommit=True) as conn,
                pytest.raises(psycopg.errors.InsufficientPrivilege),
            ):
                conn.execute("SELECT * FROM markos_control.api_credentials")

    asyncio.run(run_api(control, scenario))


@pytest.mark.parametrize("fault", ["provider", "cache", "database", "cipher"])
def test_authority_outage_fails_closed_and_redacted(control: dict[str, Any], fault: str) -> None:
    async def scenario(
        c: Client, issuer: FixtureIssuer, accounts: dict[str, str], store: ControlStore
    ) -> None:
        await c.login(issuer, accounts["both"])
        if fault == "provider":

            async def unavailable(identity: Any) -> bool:
                raise IdentityUnavailable("DO_NOT_DISCLOSE_PROVIDER_SECRET")

            issuer.valid_session = unavailable  # type: ignore[method-assign]
        elif fault == "database":
            await store._pool.close()
        else:
            with psycopg.connect(control["migration"], autocommit=True) as conn:
                if fault == "cache":
                    conn.execute(
                        "UPDATE markos_control.api_key_cache "
                        "SET payload=NULL,expires_at='-infinity'"
                    )
                else:
                    conn.execute(
                        "UPDATE markos_control.api_credentials SET access_cipher='corrupt'"
                    )
        status, result = await c.call("POST", "/api/v1/agents", agent())
        assert status == (401 if fault == "cipher" else 503), result
        assert "DO_NOT_DISCLOSE" not in str(result)
        with psycopg.connect(control["migration"]) as conn:
            assert conn.execute("SELECT count(*) FROM markos_control.agents").fetchone() == (0,)

    asyncio.run(run_api(control, scenario))


def test_auth_flow_bound_and_unused_flow_restore_fencing(control: dict[str, Any]) -> None:
    async def scenario(
        c: Client, issuer: FixtureIssuer, accounts: dict[str, str], store: ControlStore
    ) -> None:
        config = {"schema_version": 1, "challenge": challenge(opaque())}
        for _ in range(100):
            assert (await c.call("POST", "/auth/cli/start", config))[0] == 200
        assert (await c.call("POST", "/auth/cli/start", config))[0] == 429
        with psycopg.connect(control["migration"], autocommit=True) as conn:
            conn.execute("UPDATE markos_runtime.runtime_epoch SET epoch=gen_random_uuid()")
        assert (await c.call("POST", "/auth/cli/start", config))[0] == 200
        with psycopg.connect(control["migration"]) as conn:
            assert conn.execute("SELECT count(*) FROM markos_control.api_flows").fetchone() == (1,)

    asyncio.run(run_api(control, scenario))


def test_explicit_factory_and_no_queued_request_admission() -> None:
    from typing import cast

    async def scenario() -> None:
        profile = SyntheticApiConfig(schema_version=1, api_origin=ORIGIN)
        store = cast(ControlStore, object())
        issuer = FixtureIssuer()
        with pytest.raises(ValueError):
            create_app(profile, store, issuer, session_key=b"s" * 32)
        app = create_app(profile, store, issuer, session_key=b"s" * 32, confirm_synthetic=True)
        gate = asyncio.Event()

        async def receive() -> dict[str, Any]:
            await gate.wait()
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            pass

        scope = {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "headers": [(b"host", b"127.0.0.1:18443")],
        }
        tasks = [asyncio.create_task(app(scope, receive, send)) for _ in range(16)]
        try:
            await asyncio.sleep(0)
            c = Client(app)
            async with asyncio.timeout(1):
                assert (await c.call("GET", "/live"))[0] == 503
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert (await Client(app).call("GET", "/live"))[0] == 200

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["checksum", "missing", "future"])
def test_schema_incompatibility_blocks_readiness_and_api(
    control: dict[str, Any], fault: str
) -> None:
    async def scenario(
        c: Client, issuer: FixtureIssuer, accounts: dict[str, str], store: ControlStore
    ) -> None:
        await c.login(issuer, accounts["both"])
        with psycopg.connect(control["migration"], autocommit=True) as conn:
            if fault == "checksum":
                conn.execute(
                    "UPDATE markos_runtime.schema_migrations "
                    "SET sha256=repeat('0',64) WHERE version=7"
                )
            elif fault == "missing":
                conn.execute("DELETE FROM markos_runtime.schema_migrations WHERE version=7")
            else:
                conn.execute(
                    "INSERT INTO markos_runtime.schema_migrations(version,sha256) "
                    "VALUES(11,repeat('0',64))"
                )
        assert (await c.call("GET", "/live"))[0] == 200
        assert (await c.call("GET", "/ready")) == (503, {"ready": False})
        assert (await c.call("GET", "/api/v1/me"))[0] == 503

    asyncio.run(run_api(control, scenario))

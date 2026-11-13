"""Explicit fixture-only shared API. No listener, identity account or connection at import."""

import asyncio
import dataclasses
import hmac
import json
import re
import time
from collections.abc import MutableMapping
from contextlib import suppress
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from typing import Any, cast
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from markos_runtime.api.identity import (
    ISSUER,
    TOKEN_PATTERN,
    FixtureIssuer,
    IdentityDenied,
    IdentityUnavailable,
    IdentityVerifier,
    SecretCodec,
    challenge,
    opaque,
)
from markos_runtime.api.schema import EXPECTED_MIGRATIONS
from markos_runtime.contracts.base import ContractError, Domain
from markos_runtime.contracts.control import (
    AgentRevision,
    AgentStateRequest,
    ChatRequest,
    ConfigRevision,
    ControlValue,
    ConversationRequest,
)
from markos_runtime.control.api_config import (
    SyntheticApiConfig,
    SyntheticConversationApiConfig,
    SyntheticGovernanceApiConfig,
    SyntheticWebApiConfig,
)
from markos_runtime.control.store import (
    ControlConflict,
    ControlDenied,
    ControlLimited,
    ControlNotFound,
    ControlStore,
    SessionContext,
)
from markos_runtime.store.database import StorageError

COOKIE = "__Host-markos-session"
FLOW_COOKIE = "__Host-markos-flow"


class ApiFault(ValueError):
    def __init__(self, code: str, status: int) -> None:
        self.code, self.status = code, status


def wire(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value):
        return {f.name: wire(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (tuple, list)):
        return [wire(v) for v in value]
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, (UUID, datetime)):
        return str(value)
    return value


class CliStart(ControlValue):
    challenge: str


class CliExchange(ControlValue):
    state: str
    binding: str
    code: str
    verifier: str


class SyntheticApi:
    def __init__(
        self,
        config: SyntheticApiConfig | SyntheticConversationApiConfig,
        store: ControlStore,
        issuer: FixtureIssuer,
        key: bytes,
        *,
        confirm_synthetic: bool = False,
    ) -> None:
        if confirm_synthetic is not True or not isinstance(issuer, FixtureIssuer):
            raise ValueError("explicit fixture-only API confirmation required")
        self.config, self.store, self.issuer = config, store, issuer
        self.codec = SecretCodec(key)
        self.identity = IdentityVerifier(store, issuer)
        self.requests = 0
        self.failures = 0

    def context(self, raw: str) -> SessionContext:
        try:
            sid, secret = raw.split(".")
            identity = UUID(sid)
            if not identity.int or not TOKEN_PATTERN.fullmatch(secret):
                raise ValueError
            return SessionContext(identity, self.codec.lookup("session", secret))
        except (ValueError, TypeError):
            raise ApiFault("unauthenticated", 401) from None

    @staticmethod
    def cookies(request: Any) -> dict[str, str]:
        raw = cast(str, request.headers.get("cookie", ""))
        names = [part.strip().split("=", 1)[0] for part in raw.split(";") if part.strip()]
        if len(names) != len(set(names)):
            raise ApiFault("invalid", 400)
        parsed = SimpleCookie()
        try:
            parsed.load(raw)
        except Exception:
            raise ApiFault("invalid", 400) from None
        return {k: v.value for k, v in parsed.items()}

    async def authenticate(
        self, request: Any, *, local_only: bool = False
    ) -> tuple[SessionContext, dict[str, Any], str]:
        cookies = self.cookies(request)
        authorization = cast(str, request.headers.get("authorization", ""))
        if authorization and COOKIE in cookies:
            raise ApiFault("invalid", 400)
        if authorization:
            if not authorization.startswith("Bearer ") or authorization.count(" ") != 1:
                raise ApiFault("unauthenticated", 401)
            raw = authorization[7:]
            kind = "cli"
        else:
            raw = cookies.get(COOKIE, "")
            kind = "browser"
        ctx = self.context(raw)
        try:
            binding = cast(
                dict[str, Any],
                await self.store._call("api_credentials_for", ctx.session_id, ctx.lookup_hash),
            )
            if binding["kind"] != kind:
                raise IdentityDenied("credential channel mismatch")
            if not local_only:
                verified = await self.identity.verify(
                    self.codec.open(binding["access_cipher"]), binding["nonce"]
                )
                if (
                    verified.subject != binding["subject"]
                    or str(verified.provider_session_id) != binding["provider_session_id"]
                ):
                    raise IdentityDenied("credential binding mismatch")
        except (IdentityDenied, ControlDenied):
            raise ApiFault("unauthenticated", 401) from None
        return ctx, binding, raw

    def csrf(self, request: Any, raw: str, binding: dict[str, Any]) -> None:
        if binding["kind"] == "browser" and (
            request.headers.get("origin") != self.config.api_origin
            or not hmac.compare_digest(
                request.headers.get("x-csrf-token", ""), self.codec.lookup("csrf", raw)
            )
        ):
            raise ApiFault("forbidden", 403)

    async def write(
        self, request: Any, ctx: SessionContext, binding: dict[str, Any], raw: str
    ) -> None:
        self.csrf(request, raw, binding)
        if not await self.store._call("api_admit_write", ctx.session_id, ctx.lookup_hash):
            raise ApiFault("limited", 429)

    @staticmethod
    def query(request: Any, allowed: set[str]) -> dict[str, str]:
        pairs = request.query_params.multi_items()
        if len(pairs) != len(dict(pairs)) or set(dict(pairs)) - allowed:
            raise ApiFault("invalid", 400)
        return dict(pairs)

    @staticmethod
    def scope(params: dict[str, str]) -> tuple[UUID, Domain]:
        try:
            owner = UUID(params["owner_id"])
            domain = params["domain"]
            if not owner.int or domain not in {"personal", "research", "work"}:
                raise ValueError
            return owner, cast(Domain, domain)
        except (ValueError, KeyError):
            raise ApiFault("invalid", 400) from None

    async def begin(self, kind: str, pkce: str | None) -> tuple[dict[str, str], str]:
        state, binding, nonce = opaque(), opaque(), opaque()
        verifier = opaque() if kind == "browser" else None
        selected = challenge(verifier) if verifier is not None else pkce
        if selected is None or not TOKEN_PATTERN.fullmatch(selected):
            raise ApiFault("invalid", 400)
        flow = await self.store._call(
            "api_begin_flow",
            self.codec.lookup("state", state),
            self.codec.lookup("flow-binding", binding),
            self.codec.seal(verifier) if verifier else None,
            selected,
            nonce,
            kind,
        )
        params = {
            "state": state,
            "nonce": nonce,
            "code_challenge": selected,
            "code_challenge_method": "S256",
            "redirect_uri": self.config.api_origin + "/auth/callback",
            "response_type": "code",
        }
        return {
            "authorization_url": ISSUER + "/authorize?" + urlencode(params),
            "state": state,
            "flow_id": str(flow),
            "binding": binding,
        }, binding

    async def finish(
        self, kind: str, state: str, binding: str, code: str, verifier: str | None, old: str | None
    ) -> str:
        if not all(TOKEN_PATTERN.fullmatch(v) for v in (state, binding, code)):
            raise ApiFault("unauthenticated", 401)
        try:
            flow = await self.store._call(
                "api_consume_flow",
                self.codec.lookup("state", state),
                self.codec.lookup("flow-binding", binding),
                kind,
            )
            if kind == "browser":
                verifier = self.codec.open(flow["verifier_cipher"])
            if (
                verifier is None
                or not TOKEN_PATTERN.fullmatch(verifier)
                or not hmac.compare_digest(challenge(verifier), flow["challenge"])
            ):
                raise IdentityDenied("invalid PKCE binding")
            access, refresh = await self.identity.provider_call("exchange", code, verifier)
            verified = await self.identity.verify(access, flow["nonce"])
            secret = opaque()
            old_ctx = self.context(old) if old else None
            row = await self.store._call(
                "api_complete_login",
                verified.subject,
                verified.provider_session_id,
                self.codec.lookup("session", secret),
                kind,
                datetime.fromtimestamp(verified.expires_at, UTC),
                self.codec.seal(access),
                self.codec.seal(refresh),
                flow["nonce"],
                old_ctx.session_id if old_ctx else None,
                old_ctx.lookup_hash if old_ctx else None,
            )
            return str(row["session_id"]) + "." + secret
        except (IdentityDenied, ControlDenied):
            raise ApiFault("unauthenticated", 401) from None

    async def refresh(self, ctx: SessionContext, binding: dict[str, Any]) -> None:
        if binding["kind"] != "browser":
            raise ApiFault("forbidden", 403)
        access, refresh = await self.identity.provider_call(
            "refresh", self.codec.open(binding["refresh_cipher"])
        )
        verified = await self.identity.verify(access, binding["nonce"])
        await self.store._call(
            "api_refresh_credentials",
            ctx.session_id,
            ctx.lookup_hash,
            verified.subject,
            verified.provider_session_id,
            datetime.fromtimestamp(verified.expires_at, UTC),
            self.codec.seal(access),
            self.codec.seal(refresh),
        )


def create_app(
    config: SyntheticApiConfig | SyntheticConversationApiConfig,
    store: ControlStore,
    issuer: FixtureIssuer,
    *,
    session_key: bytes,
    confirm_synthetic: bool = False,
) -> Any:
    # Imports are optional and occur only after an explicit factory invocation.
    api = SyntheticApi(config, store, issuer, session_key, confirm_synthetic=confirm_synthetic)
    from fastapi import FastAPI, Request  # type: ignore[import-not-found]
    from fastapi.responses import (  # type: ignore[import-not-found]
        JSONResponse,
        PlainTextResponse,
        RedirectResponse,
    )

    from markos_runtime.api.activity import activity_page, open_stream
    from markos_runtime.web.routes import WebRoutes

    web = WebRoutes(api) if isinstance(config, SyntheticWebApiConfig) else None
    from markos_runtime.api.governance import GovernanceRoutes

    governance = GovernanceRoutes(api) if isinstance(config, SyntheticGovernanceApiConfig) else None
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)

    def page(
        params: dict[str, str], binding: dict[str, Any], owner: UUID, domain: Domain, resource: str
    ) -> tuple[int, int]:
        if not re.fullmatch(r"[0-9]{1,3}", params.get("limit", "50")):
            raise ApiFault("invalid", 400)
        limit = int(params.get("limit", "50"))
        after = 0
        if not 1 <= limit <= 100:
            raise ApiFault("invalid", 400)
        if "cursor" in params:
            try:
                cursor = json.loads(api.codec.open(params["cursor"]))
                if (
                    set(cursor)
                    != {"principal_id", "owner_id", "domain", "resource", "after", "expires"}
                    or cursor["principal_id"] != binding["principal_id"]
                    or cursor["owner_id"] != str(owner)
                    or cursor["domain"] != domain
                    or cursor["resource"] != resource
                    or cursor["expires"] <= time.time()
                    or type(cursor["after"]) is not int
                    or not 0 <= cursor["after"] <= 9223372036854775807
                ):
                    raise ValueError
                after = cursor["after"]
            except Exception:
                raise ApiFault("invalid", 400) from None
        return after, limit

    def encode_page_cursor(
        after: int | None, binding: dict[str, Any], owner: UUID, domain: Domain, resource: str
    ) -> str | None:
        if after is None:
            return None
        return api.codec.seal(
            json.dumps(
                {
                    "principal_id": binding["principal_id"],
                    "owner_id": str(owner),
                    "domain": domain,
                    "resource": resource,
                    "after": after,
                    "expires": int(time.time()) + 600,
                }
            )
        )

    async def dispatch(request: Request) -> Any:
        response: Any
        path = request.url.path
        method = request.method
        data = await request.body()
        if method == "GET" and data:
            raise ApiFault("invalid", 400)
        if (
            method == "POST"
            and request.headers.get("content-type", "").split(";")[0].strip().lower()
            != "application/json"
        ):
            raise ApiFault("invalid", 400)
        if web is not None and (path.startswith("/app") or path == "/auth/fixture/authorize"):
            return await web.handle(request, data)
        if path == "/live" and method == "GET":
            return JSONResponse({"live": True})
        if path == "/ready" and method == "GET":
            ready = bool(await store._call("api_ready", Jsonb(EXPECTED_MIGRATIONS)))
            return JSONResponse({"ready": ready}, status_code=200 if ready else 503)
        if not await store._call("api_ready", Jsonb(EXPECTED_MIGRATIONS)):
            raise ApiFault("unavailable", 503)
        if governance is not None and governance.accepts(path):
            return await governance.handle(request, data)
        if path.startswith("/auth/") and path != "/auth/callback":
            api.query(request, set())
        if path in {"/auth/login", "/auth/cli/start"} and method == "POST":
            if path == "/auth/login":
                if request.headers.get("origin") != config.api_origin:
                    raise ApiFault("forbidden", 403)
                ControlValue.from_wire(data)
                result, flow_binding = await api.begin("browser", None)
                result.pop("binding")
                response = JSONResponse(result)
                response.set_cookie(
                    FLOW_COOKIE,
                    flow_binding,
                    secure=True,
                    httponly=True,
                    samesite="lax",
                    path="/",
                    max_age=300,
                )
                return response
            start = CliStart.from_wire(data)
            result, _ = await api.begin("cli", start.challenge)
            return JSONResponse(result)
        if path == "/auth/callback" and method == "GET":
            params = api.query(request, {"code", "state"})
            raw = await api.finish(
                "browser",
                params.get("state", ""),
                api.cookies(request).get(FLOW_COOKIE, ""),
                params.get("code", ""),
                None,
                api.cookies(request).get(COOKIE),
            )
            response = (
                RedirectResponse("/app/conversations", status_code=303)
                if web is not None
                else PlainTextResponse("Synthetic sign-in complete.")
            )
            response.set_cookie(
                COOKIE, raw, secure=True, httponly=True, samesite="lax", path="/", max_age=28800
            )
            response.delete_cookie(
                FLOW_COOKIE, secure=True, httponly=True, samesite="lax", path="/"
            )
            return response
        if path == "/auth/cli/exchange" and method == "POST":
            value = CliExchange.from_wire(data)
            raw = await api.finish(
                "cli", value.state, value.binding, value.code, value.verifier, None
            )
            return JSONResponse({"access_token": raw, "token_type": "Bearer", "expires_in": 300})
        if path == "/api/v1/conversations" or path.startswith("/api/v1/conversations/"):
            if not isinstance(config, SyntheticConversationApiConfig):
                raise ApiFault("not-found", 404)
            ctx, binding, raw = await api.authenticate(request)
            params = api.query(request, {"owner_id", "domain", "cursor", "limit"})
            owner, domain = api.scope(params)
            if method == "POST":
                api.query(request, {"owner_id", "domain"})
                await api.write(request, ctx, binding, raw)
            parts = path.removeprefix("/api/v1/conversations").strip("/").split("/")
            if path == "/api/v1/conversations":
                if method == "POST":
                    conversation_request = ConversationRequest.from_wire(data)
                    if conversation_request.domain != domain:
                        raise ApiFault("invalid", 400)
                    conversation_result = await store.create_conversation(
                        ctx,
                        owner,
                        domain,
                        conversation_request.conversation_id,
                        conversation_request.operation_key,
                    )
                    return JSONResponse(wire(conversation_result), status_code=201)
                after, limit = page(params, binding, owner, domain, "conversations")
                conversation_rows = await store.read_conversations(
                    ctx, owner, domain, after=after, limit=limit
                )
                return JSONResponse(
                    {
                        "items": wire(conversation_rows),
                        "next_cursor": encode_page_cursor(
                            conversation_rows[-1].cursor if conversation_rows else None,
                            binding,
                            owner,
                            domain,
                            "conversations",
                        ),
                    }
                )
            try:
                conversation_id = UUID(parts[0])
                if not conversation_id.int:
                    raise ValueError
            except ValueError:
                raise ApiFault("not-found", 404) from None
            if len(parts) == 2 and parts[1] == "messages":
                if method == "POST":
                    request_value = ChatRequest.from_wire(data)
                    if (
                        request_value.domain != domain
                        or request_value.conversation_id != conversation_id
                    ):
                        raise ApiFault("invalid", 400)
                    receipt = await store.admit_message(ctx, owner, request_value)
                    return JSONResponse(wire(receipt), status_code=202)
                after, limit = page(
                    params, binding, owner, domain, "messages:" + str(conversation_id)
                )
                message_rows = await store.read_messages(
                    ctx, owner, domain, conversation_id, after=after, limit=limit
                )
                return JSONResponse(
                    {
                        "items": wire(message_rows),
                        "next_cursor": encode_page_cursor(
                            message_rows[-1].sequence if message_rows else None,
                            binding,
                            owner,
                            domain,
                            "messages:" + str(conversation_id),
                        ),
                    }
                )
            if len(parts) == 4 and parts[1] == "messages" and parts[3] in {"status", "cancel"}:
                api.query(request, {"owner_id", "domain"})
                try:
                    message_id = UUID(parts[2])
                except ValueError:
                    raise ApiFault("not-found", 404) from None
                cancel = parts[3] == "cancel"
                if (cancel and method != "POST") or (not cancel and method != "GET"):
                    raise ApiFault("not-found", 404)
                if cancel:
                    ControlValue.from_wire(data)
                return JSONResponse(
                    wire(
                        await store.chat_status(
                            ctx, owner, domain, conversation_id, message_id, cancel=cancel
                        )
                    )
                )
            raise ApiFault("not-found", 404)
        if path == "/api/v1/activity/stream" and method == "GET" and web is not None:
            return await open_stream(api, request)
        if path == "/api/v1/approvals" and method == "GET" and web is not None:
            ctx, binding, raw = await api.authenticate(request)
            params = api.query(request, {"owner_id", "domain"})
            owner, domain = api.scope(params)
            await store.read_agents(ctx, owner, domain)
            return JSONResponse(
                {"schema_version": 1, "available": False, "items": [], "reason": "not-enabled"}
            )
        known = {
            "/api/v1/me",
            "/api/v1/agents",
            "/api/v1/agents/control-test-agent/state",
            "/api/v1/agents/control-test-agent/revisions",
            "/api/v1/configuration",
            "/api/v1/configuration/revisions",
            "/api/v1/status",
            "/api/v1/activity",
            "/api/v1/session/logout",
            "/api/v1/session/refresh",
        }
        if path not in known:
            raise ApiFault("not-found", 404)
        ctx, binding, raw = await api.authenticate(
            request, local_only=path == "/api/v1/session/logout"
        )
        if path.startswith("/api/v1/session/"):
            api.query(request, set())
        if method == "POST":
            if path.endswith("/logout"):
                api.csrf(request, raw, binding)
            else:
                await api.write(request, ctx, binding, raw)
        if path == "/api/v1/me" and method == "GET":
            api.query(request, set())
            result = await store._call("api_me", ctx.session_id, ctx.lookup_hash)
            if binding["kind"] == "browser":
                result["csrf_token"] = api.codec.lookup("csrf", raw)
            if governance is not None:
                from markos_runtime.contracts.governance import SCHEMA_SHA256

                result["synthetic_tool_schema_sha256"] = SCHEMA_SHA256
            return JSONResponse(result)
        if path == "/api/v1/session/logout" and method == "POST":
            ControlValue.from_wire(data)
            await store._call("api_logout", ctx.session_id, ctx.lookup_hash)
            response = JSONResponse({"revoked": True})
            response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax", path="/")
            return response
        if path == "/api/v1/session/refresh" and method == "POST":
            ControlValue.from_wire(data)
            await api.refresh(ctx, binding)
            return JSONResponse({"refreshed": True})
        if path == "/api/v1/agents" and method == "POST":
            api.query(request, set())
            agent = AgentRevision.from_wire(data)
            return JSONResponse(
                wire(await store.save_agent(ctx, agent, expected_revision=agent.revision - 1)),
                status_code=201,
            )
        if (
            path in {"/api/v1/configuration", "/api/v1/configuration/revisions"}
            and method == "POST"
        ):
            api.query(request, set())
            return JSONResponse(
                wire(await store.save_configuration(ctx, ConfigRevision.from_wire(data)))
            )
        if path == "/api/v1/agents/control-test-agent/revisions" and method == "POST":
            api.query(request, set())
            agent = AgentRevision.from_wire(data)
            return JSONResponse(
                wire(await store.save_agent(ctx, agent, expected_revision=agent.revision - 1))
            )
        params = api.query(
            request,
            {"owner_id", "domain", "cursor", "limit"}
            if path == "/api/v1/activity"
            else {"owner_id", "domain"},
        )
        owner, domain = api.scope(params)
        if path == "/api/v1/agents/control-test-agent/state" and method == "POST":
            return JSONResponse(
                wire(
                    await store.set_agent_state(
                        ctx, owner, domain, AgentStateRequest.from_wire(data)
                    )
                )
            )
        if method != "GET":
            raise ApiFault("not-found", 404)
        if path == "/api/v1/agents":
            return JSONResponse(wire(await store.read_agents(ctx, owner, domain)))
        if path == "/api/v1/configuration":
            return JSONResponse(wire(await store.read_configuration(ctx, owner, domain)))
        if path == "/api/v1/status":
            return JSONResponse(
                await store._call("api_status", ctx.session_id, ctx.lookup_hash, owner, domain)
            )
        if path == "/api/v1/activity":
            return JSONResponse(
                await activity_page(api, request, ctx, binding, owner, domain, params)
            )
        raise ApiFault("not-found", 404)

    app.add_api_route("/{path:path}", dispatch, methods=["GET", "POST"])

    class Envelope:
        def __init__(self) -> None:
            self.active = 0
            self.streams = 0

        async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
            if scope["type"] != "http":
                await app(scope, receive, send)
                return
            is_stream = (
                scope.get("method") == "GET"
                and scope.get("path") == "/api/v1/activity/stream"
                and web is not None
            )
            admitted = self.streams < 20 if is_stream else self.active < 16
            if admitted:
                if is_stream:
                    self.streams += 1
                else:
                    self.active += 1
            try:
                await self.handle(scope, receive, send, admitted)
            finally:
                if admitted:
                    if is_stream:
                        self.streams -= 1
                    else:
                        self.active -= 1

        async def handle(
            self, scope: dict[str, Any], receive: Any, send: Any, admitted: bool
        ) -> None:
            if scope["type"] != "http":
                await app(scope, receive, send)
                return
            api.requests += 1
            request_id = uuid4()
            messages: list[dict[str, Any]] = []
            streaming = False
            total_stream = 0
            security = [
                (b"cache-control", b"no-store"),
                (b"pragma", b"no-cache"),
                (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                (b"x-content-type-options", b"nosniff"),
                (b"x-frame-options", b"DENY"),
                (b"referrer-policy", b"no-referrer"),
                (b"x-request-id", str(request_id).encode()),
            ]
            if web is not None and scope.get("path", "").startswith("/app"):
                security = [(k, v) for k, v in security if k != b"content-security-policy"] + [
                    (
                        b"content-security-policy",
                        b"default-src 'none'; script-src 'self'; style-src 'self'; "
                        b"connect-src 'self'; form-action 'self'; base-uri 'none'; "
                        b"frame-ancestors 'none'",
                    )
                ]

            async def collected(message: MutableMapping[str, Any]) -> None:
                nonlocal streaming, total_stream
                if message["type"] == "http.response.start" and dict(
                    message.get("headers", [])
                ).get(b"content-type", b"").startswith(b"text/event-stream"):
                    streaming = True
                    message["headers"] = [
                        (k, v)
                        for k, v in message.get("headers", [])
                        if k.lower() not in {h[0] for h in security}
                    ] + security
                if streaming:
                    total_stream += len(message.get("body", b""))
                    if len(message.get("body", b"")) > 8192 or total_stream > 262144:
                        raise TimeoutError
                    async with asyncio.timeout(2):
                        await send(message)
                    return
                if (
                    sum(len(m.get("body", b"")) for m in messages) + len(message.get("body", b""))
                    > 262144
                ):
                    raise ApiFault("unavailable", 503)
                messages.append(dict(message))

            try:
                if not admitted:
                    raise ApiFault("unavailable", 503)
                limit = (
                    610
                    if scope.get("path") == "/api/v1/activity/stream" and web is not None
                    else 10
                )
                async with asyncio.timeout(limit):
                    pairs = scope.get("headers", [])
                    if (
                        len(pairs) > 64
                        or sum(len(k) + len(v) for k, v in pairs) > 16384
                        or len(scope.get("query_string", b"")) > 8192
                    ):
                        raise ApiFault("too-large", 413)
                    sensitive = {
                        b"host",
                        b"authorization",
                        b"cookie",
                        b"origin",
                        b"content-type",
                        b"content-length",
                        b"x-csrf-token",
                        b"last-event-id",
                    }
                    names = [k.lower() for k, _ in pairs if k.lower() in sensitive]
                    if len(names) != len(set(names)):
                        raise ApiFault("invalid", 400)
                    headers = dict(pairs)
                    if (
                        scope.get("scheme") != "https"
                        or headers.get(b"host", b"").decode() != urlsplit(config.api_origin).netloc
                    ):
                        raise ApiFault("forbidden", 403)
                    origin = headers.get(b"origin")
                    if origin is not None and origin.decode() != config.api_origin:
                        raise ApiFault("forbidden", 403)
                    if headers.get(b"content-encoding", b"identity").lower() != b"identity":
                        raise ApiFault("invalid", 400)
                    length = headers.get(b"content-length")
                    if length is not None and (not length.isdigit() or int(length) > 65536):
                        raise ApiFault("too-large", 413)
                    body = bytearray()
                    async with asyncio.timeout(10):
                        while True:
                            message = await receive()
                            if message["type"] == "http.disconnect":
                                raise ApiFault("invalid", 400)
                            chunk = message.get("body", b"")
                            if len(body) + len(chunk) > 65536:
                                raise ApiFault("too-large", 413)
                            body.extend(chunk)
                            if not message.get("more_body", False):
                                break

                    replayed = False

                    async def replay() -> dict[str, Any]:
                        nonlocal replayed
                        if replayed:
                            return dict(await receive())
                        replayed = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}

                    if scope.get("method") not in {"GET", "POST"}:
                        raise ApiFault("not-found", 404)
                    await app(scope, replay, collected)
            except Exception as error:
                if streaming:
                    with suppress(Exception):
                        async with asyncio.timeout(2):
                            await send(
                                {"type": "http.response.body", "body": b"", "more_body": False}
                            )
                    return
                api.failures += 1
                if isinstance(error, ApiFault):
                    code, status = error.code, error.status
                elif isinstance(error, ControlConflict):
                    code, status = "conflict", 409
                elif isinstance(error, ControlLimited):
                    code, status = "limited", 429
                elif isinstance(error, ControlNotFound):
                    code, status = "not-found", 404
                elif isinstance(error, ControlDenied):
                    code, status = "forbidden", 403
                elif isinstance(error, (IdentityDenied,)):
                    code, status = "unauthenticated", 401
                elif isinstance(error, ContractError):
                    code, status = "invalid", 400
                elif isinstance(error, (IdentityUnavailable, StorageError, TimeoutError)):
                    code, status = "unavailable", 503
                else:
                    code, status = "unavailable", 503
                error_body = {"schema_version": 1, "request_id": str(request_id), "code": code}
                messages = []
                await JSONResponse(error_body, status_code=status)(scope, receive, collected)
            for message in messages:
                if message["type"] == "http.response.start":
                    message["headers"] = [
                        (k, v)
                        for k, v in message.get("headers", [])
                        if k.lower() not in {h[0] for h in security}
                    ] + security
                await send(message)

    return Envelope()

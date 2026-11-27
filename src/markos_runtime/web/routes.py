"""Static shells, fixture login and Google sign-in; private data comes only from the shared API."""

import hmac
import json
import time
from importlib.resources import files
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from psycopg.types.json import Jsonb

from markos_runtime.api.application import FLOW_COOKIE, GOOGLE_CALLBACK_PATH, ApiFault
from markos_runtime.api.identity import TOKEN_PATTERN, opaque
from markos_runtime.api.schema import EXPECTED_MIGRATIONS
from markos_runtime.contracts.control import ControlValue

FIXTURE_COOKIE = "__Host-markos-fixture"
# Binds Google's callback to the browser that started the sign-in. Sealed, 10 minutes.
GOOGLE_COOKIE = "__Host-markos-google"
GOOGLE_CALLBACK_PARAMS = {"state", "code", "scope", "authuser", "prompt", "hd", "iss", "error"}


class FixtureAuthorization(ControlValue):
    request: str
    proof: str
    credential: str


class WebRoutes:
    def __init__(self, api: Any) -> None:
        self.api = api

    async def _issuer(self, operation: str, *args: str) -> Any:
        """One issuer call under the shared sign-in admission lease."""
        if not await self.api.store._call("api_ready", Jsonb(EXPECTED_MIGRATIONS)):
            raise ApiFault("unavailable", 503)
        lease = await self.api.store._call("api_auth_claim")
        if lease is None:
            raise ApiFault("unavailable", 503)
        try:
            return await getattr(self.api.issuer, operation)(*args)
        finally:
            await self.api.store._call("api_auth_release", UUID(str(lease)))

    async def google_start(self, params: dict[str, str]) -> Any:
        from fastapi.responses import RedirectResponse  # type: ignore[import-not-found]

        started = await self._issuer("google_begin", params["code_challenge"], params["nonce"])
        binding = {
            "google_state": started["state"],
            "state": params["state"],
            "mode": params["mode"],
            "expires": int(time.time()) + 600,
        }
        response = RedirectResponse(started["url"], status_code=303)
        response.set_cookie(
            GOOGLE_COOKIE,
            self.api.codec.seal(json.dumps(binding)),
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=600,
        )
        return response

    async def google_callback(self, request: Any) -> tuple[str, str]:
        """Checks Google's callback against the browser's cookie. Returns the mode and code."""
        params = self.api.query(request, GOOGLE_CALLBACK_PARAMS)
        try:
            bound = json.loads(
                self.api.codec.open(self.api.cookies(request).get(GOOGLE_COOKIE, ""))
            )
            if (
                set(bound) != {"google_state", "state", "mode", "expires"}
                or bound["expires"] <= time.time()
                or bound["mode"] not in {"browser", "cli"}
            ):
                raise ValueError
        except Exception:
            raise ApiFault("unauthenticated", 401) from None
        if (
            "error" in params
            or not params.get("code")
            or not hmac.compare_digest(params.get("state", ""), bound["google_state"])
        ):
            raise ApiFault("unauthenticated", 401)
        code = str(await self._issuer("google_finish", params["state"], params["code"]))
        if bound["mode"] == "cli":
            return "cli", code
        return "browser", "/auth/callback?" + urlencode({"state": bound["state"], "code": code})

    async def handle(self, request: Any, body: bytes) -> Any:
        from fastapi.responses import (  # type: ignore[import-not-found]
            HTMLResponse,
            JSONResponse,
            RedirectResponse,
            Response,
        )
        from jinja2 import (  # type: ignore[import-not-found]
            Environment,
            StrictUndefined,
            select_autoescape,
        )

        response: Any
        path, method = request.url.path, request.method
        assets = {
            "/app/assets/app.js": ("app.js", "text/javascript"),
            "/app/assets/app.css": ("app.css", "text/css"),
        }
        if path in assets and method == "GET":
            name, kind = assets[path]
            return Response(
                files("markos_runtime.web").joinpath("assets", name).read_bytes(), media_type=kind
            )
        pages = {
            "/app": "Conversations",
            "/app/conversations": "Conversations",
            "/app/agents": "Agents and health",
            "/app/activity": "Activity",
            "/app/approvals": "Approvals",
            "/app/configuration": "Configuration",
            "/app/sign-in": "Sign in",
        }
        if hasattr(self.api.config, "bill_profile"):
            pages["/app/bills"] = "Electricity bills"
        if getattr(self.api, "mail_available", False):
            pages["/app/mail"] = "Mail"
        context: dict[str, Any] = {
            "title": pages.get(path, "Sign in"),
            "page": path.removeprefix("/app/") if path != "/app" else "conversations",
            "request": "",
            "proof": "",
            "bill_knowledge": hasattr(self.api.config, "bill_profile"),
            "mail": getattr(self.api, "mail_available", False),
            "synthetic_governance": hasattr(self.api.config, "governance_profile"),
            "code": "",
        }
        response_cookie = None
        google = getattr(self.api, "google_signin", False)
        if path == "/app/authorize" and method == "GET":
            params = self.api.query(request, {"state", "nonce", "code_challenge", "mode"})
            if (
                set(params) != {"state", "nonce", "code_challenge", "mode"}
                or params["mode"] not in {"browser", "cli"}
                or not all(
                    TOKEN_PATTERN.fullmatch(params[k]) for k in ("state", "nonce", "code_challenge")
                )
            ):
                raise ApiFault("invalid", 400)
            if params["mode"] == "browser" and not TOKEN_PATTERN.fullmatch(
                self.api.cookies(request).get(FLOW_COOKIE, "")
            ):
                raise ApiFault("unauthenticated", 401)
            if google:
                return await self.google_start(params)
            response_cookie = opaque()
            context.update(
                page="authorize",
                request=self.api.codec.seal(
                    json.dumps({**params, "expires": int(time.time()) + 300})
                ),
                proof=self.api.codec.lookup("fixture-csrf", response_cookie),
            )
        elif path == GOOGLE_CALLBACK_PATH and method == "GET" and google:
            mode, target = await self.google_callback(request)
            if mode == "browser":
                response = RedirectResponse(target, status_code=303)
                response.delete_cookie(
                    GOOGLE_COOKIE, secure=True, httponly=True, samesite="lax", path="/"
                )
                return response
            context.update(title="Sign-in code", page="code", code=target)
        elif path == "/auth/fixture/authorize" and method == "POST":
            if google:
                raise ApiFault("not-found", 404)
            if request.headers.get("origin") != self.api.config.api_origin:
                raise ApiFault("forbidden", 403)
            if not await self.api.store._call("api_ready", Jsonb(EXPECTED_MIGRATIONS)):
                raise ApiFault("unavailable", 503)
            value = FixtureAuthorization.from_wire(body)
            binding = self.api.cookies(request).get(FIXTURE_COOKIE, "")
            if not TOKEN_PATTERN.fullmatch(binding) or not hmac.compare_digest(
                value.proof, self.api.codec.lookup("fixture-csrf", binding)
            ):
                raise ApiFault("forbidden", 403)
            try:
                params = json.loads(self.api.codec.open(value.request))
                if (
                    set(params) != {"state", "nonce", "code_challenge", "mode", "expires"}
                    or params["expires"] <= time.time()
                    or params["mode"] not in {"browser", "cli"}
                ):
                    raise ValueError
            except Exception:
                raise ApiFault("invalid", 400) from None
            lease = await self.api.store._call("api_auth_claim")
            if lease is None:
                raise ApiFault("unavailable", 503)
            try:
                code = await self.api.issuer.authorize_async(
                    value.credential, params["code_challenge"], params["nonce"]
                )
            finally:
                await self.api.store._call("api_auth_release", UUID(str(lease)))
            result = {"mode": params["mode"]}
            if params["mode"] == "browser":
                result["callback_path"] = "/auth/callback?" + urlencode(
                    {"state": params["state"], "code": code}
                )
            else:
                result["code"] = code
            response = JSONResponse(result)
            response.delete_cookie(
                FIXTURE_COOKIE, secure=True, httponly=True, samesite="lax", path="/"
            )
            return response
        elif path not in pages or method != "GET":
            raise ApiFault("not-found", 404)
        else:
            self.api.query(request, set())
        # Template and assets are fixed package resources; URL paths never select files.
        env = Environment(autoescape=select_autoescape(default=True), undefined=StrictUndefined)
        template = env.from_string(
            files("markos_runtime.web").joinpath("assets", "shell.html").read_text()
        )
        response = HTMLResponse(template.render(**context))
        if context["page"] == "code":
            response.delete_cookie(
                GOOGLE_COOKIE, secure=True, httponly=True, samesite="lax", path="/"
            )
        if response_cookie:
            response.set_cookie(
                FIXTURE_COOKIE,
                response_cookie,
                secure=True,
                httponly=True,
                samesite="lax",
                path="/",
                max_age=300,
            )
        return response

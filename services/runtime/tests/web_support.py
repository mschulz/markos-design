"""Temporary TLS server and explicit M1-E fixture; no production listener or account."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn  # type: ignore[import-not-found]

from markos_runtime.api.application import create_app
from markos_runtime.api.identity import FixtureIssuer, opaque
from markos_runtime.control.api_config import SyntheticWebApiConfig
from markos_runtime.control.store import ControlStore
from tests.control_api.test_https import HttpClient as FixtureBrowserClient
from tests.control_api.test_https import certificate
from tests.integration.test_control_persistence import prepare, session


@asynccontextmanager
async def web_server(
    data: dict[str, Any], directory: Path, *, prepare_agent: bool = True
) -> AsyncIterator[dict[str, Any]]:
    cert, key = certificate(directory)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(32)
    origin = "https://127.0.0.1:" + str(listener.getsockname()[1])
    issuer = FixtureIssuer()
    credentials = {name: opaque() for name in data["principals"]}
    for name, credential in credentials.items():
        issuer.add_account(name, credential)
    async with ControlStore.connect(data["gateway"]) as store:
        if prepare_agent:
            await prepare(store, await session(store))
        profile = SyntheticWebApiConfig(
            schema_version=1,
            api_origin=origin,
            conversation_profile="deterministic-conversation-v1",
            web_profile="synthetic-web-cli-v1",
        )
        app = create_app(profile, store, issuer, session_key=b"s" * 32, confirm_synthetic=True)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                ssl_certfile=str(cert),
                ssl_keyfile=str(key),
                proxy_headers=False,
                access_log=False,
                log_config=None,
                lifespan="off",
                limit_concurrency=48,
                timeout_graceful_shutdown=2,
            )
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    await asyncio.sleep(0.01)
            yield {
                "origin": origin,
                "port": listener.getsockname()[1],
                "cert": cert,
                "key": key,
                "issuer": issuer,
                "credentials": credentials,
                "store": store,
                "app": app,
                "server": server,
            }
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 8)
            listener.close()


class WebBrowserClient(FixtureBrowserClient):
    async def login(self, issuer: FixtureIssuer, credential: str, *, cli: bool = False) -> str:
        from urllib.parse import parse_qs, urlencode, urlsplit

        from markos_runtime.api.application import COOKIE

        if cli:
            return await super().login(issuer, credential, cli=True)
        status, flow = await self.call("POST", "/auth/login", {"schema_version": 1})
        assert status == 200
        params = parse_qs(urlsplit(flow["authorization_url"]).query)
        code = issuer.authorize(credential, params["code_challenge"][0], params["nonce"][0])
        status, result = await self.call(
            "GET", "/auth/callback?" + urlencode({"code": code, "state": flow["state"]})
        )
        assert status == 303 and dict(self.last_headers)[b"location"] == b"/app/conversations", (
            result
        )
        status, me = await self.call("GET", "/api/v1/me")
        assert status == 200
        self.csrf = me["csrf_token"]
        return self.cookies[COOKIE]

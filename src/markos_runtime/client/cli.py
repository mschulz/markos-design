"""Explicit synthetic shared-API commands; credentials never enter arguments or output."""

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import secrets
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, Never
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

from markos_runtime.client.http import ClientError, HttpClient
from markos_runtime.contracts.base import ContractError
from markos_runtime.contracts.control import (
    AgentRevision,
    AgentStateRequest,
    ChatRequest,
    ConfigRevision,
    ControlValue,
    ConversationRequest,
    digest_document,
)


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise ClientError("invalid-command", 400)


def output(value: Any) -> None:
    # JSON escaping prevents untrusted control characters from becoming terminal commands.
    print(json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True))


def load(model: type[ControlValue], path: str) -> ControlValue:
    if path == "-":
        body = sys.stdin.buffer.read(65537)
    else:
        with Path(path).open("rb") as source:
            body = source.read(65537)
    return model.from_wire(body)


def main(argv: list[str] | None = None) -> int:
    parser = Parser(
        prog="markos-control",
        description="Synthetic workspace client. All operations use the shared HTTPS API.",
    )
    parser.add_argument("--origin", required=True)
    parser.add_argument("--ca-file", type=Path, required=True)
    parser.add_argument("--session-file", type=Path, required=True)
    parser.add_argument("--confirm-synthetic", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    commands = [
        "login",
        "logout",
        "me",
        "status",
        "agents",
        "agent-register",
        "agent-state",
        "conversations",
        "conversation-create",
        "messages",
        "send",
        "message-status",
        "cancel",
        "activity",
        "watch",
        "configuration",
        "configuration-preview",
        "configuration-apply",
        "approvals",
        "tool-grant",
        "tool-grant-preview",
        "tool-grant-apply",
        "tool-send",
        "approval-decide",
        "tool-dispatches",
        "tool-audit",
        "tool-reconcile",
    ]
    for name in commands:
        item = sub.add_parser(name)
        if name == "login":
            item.add_argument("--open-browser", action="store_true")
        if name not in {"login", "logout", "me"}:
            item.add_argument("--owner-id")
            item.add_argument("--domain", choices=["personal", "research", "work"])
        if name in {
            "agent-register",
            "agent-state",
            "conversation-create",
            "send",
            "configuration-preview",
            "configuration-apply",
            "tool-grant-preview",
            "tool-grant-apply",
            "tool-send",
            "approval-decide",
            "tool-reconcile",
        }:
            item.add_argument(
                "--input",
                required=True,
                help="Explicit JSON file or - for stdin; never message text in arguments",
            )
        if name in {
            "agent-register",
            "agent-state",
            "configuration-apply",
            "tool-grant-apply",
            "approval-decide",
            "tool-reconcile",
        }:
            item.add_argument("--confirm-digest")
        if name in {"messages", "send", "message-status", "cancel"}:
            item.add_argument("--conversation-id", type=UUID, required=True)
        if name in {"message-status", "cancel"}:
            item.add_argument("--message-id", type=UUID, required=True)
        if name in {
            "conversations",
            "messages",
            "activity",
            "watch",
            "approvals",
            "tool-dispatches",
            "tool-audit",
        }:
            item.add_argument("--cursor")
            item.add_argument("--limit", type=int, default=50)
        if name == "watch":
            item.add_argument("--max-events", type=int, default=100)
            item.add_argument("--seconds", type=int, default=600)
    try:
        args = parser.parse_args(argv)
        client = HttpClient(
            args.origin,
            args.ca_file,
            session_file=args.session_file,
            confirm_synthetic=args.confirm_synthetic,
        )
        command = args.command
        if command == "login":
            if not sys.stdin.isatty():
                raise ClientError("interactive-sign-in-requires-terminal", 400)
            verifier = secrets.token_urlsafe(32)
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            flow = client.request(
                "/auth/cli/start", {"schema_version": 1, "challenge": challenge}, public=True
            )
            parameters = parse_qs(urlsplit(flow["authorization_url"]).query)
            url = (
                args.origin
                + "/app/authorize?"
                + urlencode(
                    {
                        "state": flow["state"],
                        "nonce": parameters["nonce"][0],
                        "code_challenge": challenge,
                        "mode": "cli",
                    }
                )
            )
            print("Open this local test sign-in page, then paste its one-use code:")
            print(url)
            if args.open_browser:
                webbrowser.open(url, new=2, autoraise=True)
            code = getpass.getpass("One-use authorization code: ")
            if len(code) != 43:
                raise ClientError("invalid-authorization-code", 400)
            response = client.request(
                "/auth/cli/exchange",
                {
                    "schema_version": 1,
                    "state": flow["state"],
                    "binding": flow["binding"],
                    "code": code,
                    "verifier": verifier,
                },
                public=True,
            )
            if (
                response.get("token_type") != "Bearer"
                or type(response.get("expires_in")) is not int
                or not 1 <= response["expires_in"] <= 300
            ):
                raise ClientError("invalid-response")
            assert client.session is not None
            client.session.write(
                {
                    "schema_version": 1,
                    "api_origin": args.origin,
                    "bearer": response["access_token"],
                    "expires_at": int(time.time()) + response["expires_in"],
                }
            )
            print("Signed in to the synthetic workspace for at most five minutes.")
            return 0
        if command == "logout":
            assert client.session is not None
            try:
                client.request("/api/v1/session/logout", {"schema_version": 1})
            finally:
                client.session.remove()
            print("Signed out; local session removed.")
            return 0
        if command == "me":
            output(client.request("/api/v1/me"))
            return 0
        try:
            owner = UUID(args.owner_id)
            if not owner.int or args.domain not in {"personal", "research", "work"}:
                raise ValueError
        except (ValueError, TypeError):
            raise ClientError("explicit-scope-required", 400) from None
        query = {"owner_id": str(owner), "domain": args.domain}

        def scoped(path: str) -> str:
            return path + "?" + urlencode(query)

        if command in {
            "tool-grant",
            "tool-grant-preview",
            "tool-grant-apply",
            "tool-send",
            "approval-decide",
            "tool-dispatches",
            "tool-audit",
            "tool-reconcile",
        }:
            from markos_runtime.client.governance import execute

            return execute(client, args, owner, query, load, output)
        if command == "watch":
            if not 1 <= args.max_events <= 100 or not 1 <= args.seconds <= 600:
                raise ClientError("invalid-stream-bound", 400)
            remaining, last = args.max_events, args.cursor
            deadline = time.monotonic() + args.seconds
            for attempt in range(3):
                if time.monotonic() >= deadline:
                    return 0
                try:
                    for event in client.activity(
                        str(owner),
                        args.domain,
                        cursor=last,
                        max_events=remaining,
                        seconds=max(1, min(600, int(deadline - time.monotonic()) + 1)),
                    ):
                        output(event)
                        last = event["cursor"]
                        remaining -= 1
                    if remaining == 0 or time.monotonic() >= deadline:
                        return 0
                except ClientError as error:
                    if error.status not in {429, 503} or attempt == 2:
                        raise
                time.sleep(max(0, min(2**attempt, deadline - time.monotonic())))
            raise ClientError("activity-reconnect-limit", 503)
        if command in {
            "agent-register",
            "agent-state",
            "configuration-preview",
            "configuration-apply",
            "conversation-create",
            "send",
            "tool-grant-preview",
            "tool-grant-apply",
            "tool-send",
            "approval-decide",
            "tool-reconcile",
        }:
            models: dict[str, type[ControlValue]] = {
                "agent-register": AgentRevision,
                "agent-state": AgentStateRequest,
                "configuration-preview": ConfigRevision,
                "configuration-apply": ConfigRevision,
                "conversation-create": ConversationRequest,
                "send": ChatRequest,
            }
            value = load(models[command], args.input)
            if hasattr(value, "owner_id") and value.owner_id != owner:
                raise ClientError("input-scope-mismatch", 400)
            if hasattr(value, "domain") and value.domain != args.domain:
                raise ClientError("input-scope-mismatch", 400)
            if isinstance(value, ConfigRevision):
                current = client.request(scoped("/api/v1/configuration"))
                if value.previous_revision != (current["revision"] if current else 0):
                    raise ClientError("conflict", 409)
                agents = client.request(scoped("/api/v1/agents"))
                if not agents or agents[0]["manifest"] != value.agent.model_dump(mode="json"):
                    raise ClientError("conflict", 409)
            if command in {
                "agent-register",
                "agent-state",
                "configuration-preview",
                "configuration-apply",
            }:
                data = value.model_dump(mode="json")
                digest = digest_document(data)
                confirmed = getattr(args, "confirm_digest", None)
                if command == "configuration-preview" or confirmed is None:
                    output({"action": command, "preview": data, "digest": digest, "applied": False})
                    return 0
                if not hmac.compare_digest(confirmed, digest):
                    raise ClientError("preview-digest-mismatch", 409)
            routes = {
                "agent-register": "/api/v1/agents",
                "agent-state": scoped("/api/v1/agents/control-test-agent/state"),
                "configuration-apply": "/api/v1/configuration/revisions",
                "conversation-create": scoped("/api/v1/conversations"),
            }
            if command == "send":
                assert isinstance(value, ChatRequest)
                if value.conversation_id != args.conversation_id:
                    raise ClientError("input-conversation-mismatch", 400)
                path = scoped("/api/v1/conversations/" + str(args.conversation_id) + "/messages")
            else:
                path = routes[command]
            output(client.request(path, value))
            return 0
        paths = {
            "status": "/api/v1/status",
            "agents": "/api/v1/agents",
            "conversations": "/api/v1/conversations",
            "activity": "/api/v1/activity",
            "configuration": "/api/v1/configuration",
            "approvals": "/api/v1/approvals",
        }
        if command in {"messages", "message-status", "cancel"}:
            path = "/api/v1/conversations/" + str(args.conversation_id) + "/messages"
            if command != "messages":
                path += (
                    "/" + str(args.message_id) + ("/cancel" if command == "cancel" else "/status")
                )
        else:
            path = paths[command]
        if command in {"messages", "conversations", "activity", "approvals"}:
            if not 1 <= args.limit <= 100:
                raise ClientError("invalid-page-bound", 400)
            query["limit"] = str(args.limit)
            if args.cursor:
                query["cursor"] = args.cursor
        output(client.request(scoped(path), {"schema_version": 1} if command == "cancel" else None))
        return 0
    except (ClientError, ContractError, OSError, ValueError, KeyError) as error:
        code = error.code if isinstance(error, ClientError) else "invalid-explicit-request"
        print("Request failed: " + code + ". No completion is assumed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

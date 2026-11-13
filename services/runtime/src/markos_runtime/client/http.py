"""Bounded loopback HTTPS client and explicit private session-file boundary."""

import http.client
import json
import math
import os
import re
import ssl
import stat
import tempfile
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from markos_runtime.contracts.base import ContractError
from markos_runtime.contracts.control import ControlValue
from markos_runtime.control.api_config import SyntheticApiConfig
from markos_runtime.http_bounds import bounded_connection


class ClientError(ValueError):
    def __init__(self, code: str = "unavailable", status: int = 503) -> None:
        self.code, self.status = code, status
        super().__init__(code)


def parse_json(body: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise ValueError
            output[key] = value
        return output

    def invalid(value: str) -> object:
        raise ValueError

    try:
        if len(body) > 262144:
            raise ValueError

        def floating(value: str) -> float:
            result = float(value)
            if not math.isfinite(result):
                raise ValueError
            return result

        return json.loads(
            body, object_pairs_hook=unique, parse_constant=invalid, parse_float=floating
        )
    except (ValueError, UnicodeError, RecursionError):
        raise ClientError("invalid-response") from None


def document(body: bytes) -> dict[str, Any]:
    value = parse_json(body)
    if not isinstance(value, dict):
        raise ClientError("invalid-response")
    return dict(value)


def check_session(value: dict[str, Any]) -> None:
    try:
        if (
            set(value) != {"schema_version", "api_origin", "bearer", "expires_at"}
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or type(value["expires_at"]) is not int
            or not isinstance(value["api_origin"], str)
            or not isinstance(value["bearer"], str)
            or not re.fullmatch(r"[0-9a-f-]{36}\.[A-Za-z0-9_-]{43}", value["bearer"])
            or not UUID(value["bearer"].split(".")[0]).int
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise ClientError("invalid-session-file", 400) from None


class SessionFile:
    def __init__(self, path: Path) -> None:
        if not path.is_absolute():
            raise ClientError("explicit-private-session-file-required", 400)
        parent = path.parent.resolve()
        self.path = parent / path.name
        try:
            metadata = parent.stat()
            if not parent.is_dir() or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                raise ClientError("private-session-directory-required", 400)
            if any((ancestor / ".git").exists() for ancestor in (parent, *parent.parents)):
                raise ClientError("session-file-must-stay-outside-git", 400)
            if self.path.is_symlink():
                raise ClientError("unsafe-session-file", 400)
        except OSError:
            raise ClientError("private-session-directory-required", 400) from None

    def read(self) -> dict[str, Any]:
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                    or info.st_size > 4096
                ):
                    raise ClientError("unsafe-session-file", 400)
                value = document(source.read(4097))
            check_session(value)
            return value
        except OSError:
            raise ClientError("session-file-unavailable", 401) from None

    def write(self, value: dict[str, Any]) -> None:
        check_session(value)
        if self.path.exists():
            self.read()
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".markos-session-", dir=self.path.parent
            )
            with os.fdopen(descriptor, "w") as output:
                os.fchmod(output.fileno(), 0o600)
                json.dump(value, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except OSError:
            raise ClientError("session-file-write-failed", 500) from None
        finally:
            if temporary:
                with suppress(OSError):
                    os.unlink(temporary)

    def remove(self) -> None:
        self.read()
        try:
            self.path.unlink()
        except OSError:
            raise ClientError("session-file-remove-failed", 500) from None


class HttpClient:
    def __init__(
        self,
        origin: str,
        ca_file: Path,
        *,
        session_file: Path | None = None,
        confirm_synthetic: bool = False,
    ) -> None:
        if confirm_synthetic is not True:
            raise ClientError("explicit-synthetic-confirmation-required", 400)
        try:
            SyntheticApiConfig(schema_version=1, api_origin=origin)
            self.tls = ssl.create_default_context(cafile=str(ca_file))
        except (ContractError, ValueError, OSError):
            raise ClientError("invalid-explicit-https-configuration", 400) from None
        self.origin = origin
        self.url = urlsplit(origin)
        self.session = SessionFile(session_file) if session_file else None

    def bearer(self) -> str:
        if self.session is None:
            raise ClientError("session-file-required", 401)
        value = self.session.read()
        if value["api_origin"] != self.origin:
            raise ClientError("session-origin-mismatch", 401)
        if value["expires_at"] <= time.time():
            raise ClientError("session-expired", 401)
        return str(value["bearer"])

    def _connection(self) -> http.client.HTTPSConnection:
        assert self.url.hostname is not None
        return http.client.HTTPSConnection(
            self.url.hostname, self.url.port, context=self.tls, timeout=10
        )

    def request(
        self, path: str, body: ControlValue | dict[str, Any] | None = None, *, public: bool = False
    ) -> Any:
        if not path.startswith(("/api/v1/", "/auth/cli/")) or path.startswith("//") or "#" in path:
            raise ClientError("invalid-client-route", 400)
        payload = (
            body.to_wire()
            if isinstance(body, ControlValue)
            else (json.dumps(body, allow_nan=False).encode() if body is not None else None)
        )
        if payload is not None and len(payload) > 65536:
            raise ClientError("too-large", 413)
        headers = {"Accept": "application/json"}
        if not public:
            headers["Authorization"] = "Bearer " + self.bearer()
        if payload is not None:
            headers["Content-Type"] = "application/json"
        connection = self._connection()
        try:
            with bounded_connection(connection, time.monotonic() + 10):
                connection.request(
                    "POST" if payload is not None else "GET", path, body=payload, headers=headers
                )
                response = connection.getresponse()
                raw = response.read(262145)
                if len(raw) > 262144:
                    raise ClientError("too-large", 413)
                if response.status not in {200, 201, 202}:
                    error = document(raw)
                    code = error.get("code", "unavailable")
                    if code not in {
                        "invalid",
                        "unauthenticated",
                        "forbidden",
                        "not-found",
                        "conflict",
                        "too-large",
                        "limited",
                        "unavailable",
                        "resync-required",
                    }:
                        code = "unavailable"
                    raise ClientError(str(code), response.status)
                if response.getheader("Content-Type", "").split(";")[0] != "application/json":
                    raise ClientError("invalid-response")

                value = parse_json(raw)
                if isinstance(value, dict):
                    return document(raw)
                if isinstance(value, list) or value is None:
                    return value
                raise ClientError("invalid-response")
        except (OSError, ValueError, http.client.HTTPException) as error:
            if isinstance(error, ClientError):
                raise
            raise ClientError() from None
        finally:
            connection.close()

    def activity(
        self,
        owner: str,
        domain: str,
        *,
        cursor: str | None = None,
        max_events: int = 100,
        seconds: int = 600,
    ) -> Iterator[dict[str, Any]]:
        if not 1 <= max_events <= 100 or not 1 <= seconds <= 600:
            raise ClientError("invalid-stream-bound", 400)
        path = "/api/v1/activity/stream?" + urlencode({"owner_id": owner, "domain": domain})
        headers = {"Accept": "text/event-stream", "Authorization": "Bearer " + self.bearer()}
        if cursor:
            if len(cursor) > 2048 or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", cursor):
                raise ClientError("invalid-cursor", 400)
            headers["Last-Event-ID"] = cursor
        connection = self._connection()
        deadline = time.monotonic() + seconds
        total = 0
        count = 0
        try:
            with bounded_connection(connection, deadline):
                connection.request("GET", path, headers=headers)
                response = connection.getresponse()
                if response.status != 200:
                    code = document(response.read(4097)).get("code", "unavailable")
                    if code not in {
                        "invalid",
                        "unauthenticated",
                        "forbidden",
                        "not-found",
                        "conflict",
                        "too-large",
                        "limited",
                        "unavailable",
                        "resync-required",
                    }:
                        code = "unavailable"
                    raise ClientError(str(code), response.status)
                if response.getheader("Content-Type", "").split(";")[0] != "text/event-stream":
                    raise ClientError("invalid-response")
                frame = bytearray()
                while time.monotonic() < deadline and count < max_events:
                    if connection.sock is not None:
                        connection.sock.settimeout(max(0.01, min(10, deadline - time.monotonic())))
                    try:
                        line = response.readline(8193)
                    except TimeoutError:
                        if time.monotonic() >= deadline:
                            return
                        raise
                    if not line:
                        return
                    frame.extend(line)
                    total += len(line)
                    if len(frame) > 8192 or total > 262144:
                        raise ClientError("too-large", 413)
                    if line != b"\n":
                        continue
                    fields: dict[str, str] = {}
                    for part in frame.decode().splitlines():
                        if ": " in part:
                            key, value = part.split(": ", 1)
                            if key in fields:
                                raise ClientError("invalid-response")
                            fields[key] = value
                    frame.clear()
                    if fields.get("event") == "resync-required":
                        raise ClientError("resync-required", 409)
                    if fields.get("event") == "activity":
                        if not fields.get("id"):
                            raise ClientError("invalid-response")
                        yield {"cursor": fields["id"], "item": document(fields["data"].encode())}
                        count += 1
        except (OSError, ValueError, KeyError, http.client.HTTPException) as error:
            if isinstance(error, ClientError):
                raise
            raise ClientError() from None
        finally:
            connection.close()

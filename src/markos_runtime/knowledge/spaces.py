"""Bill originals in an S3-compatible bucket (DigitalOcean Spaces), with SigV4 signing on httpx2.

Same interface as BillObjects: keys are scoped by owner and domain and named by the SHA-256 of
the body. Every read checks that hash. The bucket stays private: requests are always signed,
uploads ask for a private ACL, and redirects are never followed. SigV4 needs the request time;
that is a protocol timestamp only, never stored.
"""

import hashlib
import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID
from xml.etree import ElementTree

import httpx2  # type: ignore[import-not-found,unused-ignore]

from markos_runtime.knowledge.objects import LIMITS, BillObjectError

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
BUCKET = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")
REGION = re.compile(r"[a-z0-9-]{1,32}")
PREFIX = re.compile(r"[a-z0-9-]{1,32}")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def signature_key(secret: str, day: str, region: str) -> bytes:
    key = ("AWS4" + secret).encode()
    for part in (day, region, "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return key


def authorization(
    method: str,
    path: str,
    headers: dict[str, str],
    *,
    access_key: str,
    secret_key: str,
    region: str,
    query: str = "",
) -> str:
    """Return the SigV4 Authorization header.

    Headers must hold host, x-amz-date and x-amz-content-sha256.
    """
    names = sorted(name.lower() for name in headers)
    values = {name.lower(): " ".join(value.strip().split()) for name, value in headers.items()}
    signed = ";".join(names)
    canonical = "\n".join(
        [
            method,
            quote(path, safe="/-_.~"),
            query,
            "".join(f"{name}:{values[name]}\n" for name in names),
            signed,
            values["x-amz-content-sha256"],
        ]
    )
    stamp = values["x-amz-date"]
    scope = f"{stamp[:8]}/{region}/s3/aws4_request"
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()]
    )
    key = signature_key(secret_key, stamp[:8], region)
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    credential = f"Credential={access_key}/{scope}"
    return f"AWS4-HMAC-SHA256 {credential}, SignedHeaders={signed}, Signature={signature}"


@dataclass
class SpacesObjects:
    endpoint: str
    region: str
    bucket: str
    access_key: str
    secret_key: str = field(repr=False)
    prefix: str = "bills"
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC), repr=False)
    transport: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        parts = urlsplit(self.endpoint)
        secure = parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK)
        if (
            not secure
            or not parts.hostname
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
            or parts.username
            or parts.password
        ):
            raise BillObjectError("HTTPS object store endpoint required")
        if not (
            BUCKET.fullmatch(self.bucket)
            and REGION.fullmatch(self.region)
            and PREFIX.fullmatch(self.prefix)
            and self.access_key
            and self.secret_key
        ):
            raise BillObjectError("valid object store settings required")
        self.base = f"{parts.scheme}://{parts.netloc}"
        self.host = parts.netloc
        self.http = httpx2.Client(
            timeout=httpx2.Timeout(30.0, connect=5.0),
            limits=httpx2.Limits(max_connections=4),
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        )

    def key(self, owner: UUID, domain: str, digest: str, suffix: str) -> str:
        if not owner.int or domain not in {"personal", "research", "work"}:
            raise BillObjectError("valid bill scope required")
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or suffix not in LIMITS:
            raise BillObjectError("invalid bill source reference")
        return f"/{self.bucket}/{self.prefix}/{owner}/{domain}/{digest}{suffix}"

    def request(self, method: str, path: str, body: bytes = b"", query: str = "") -> Any:
        headers = {
            "host": self.host,
            "x-amz-date": self.clock().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"),
            "x-amz-content-sha256": hashlib.sha256(body).hexdigest() if body else EMPTY_SHA256,
        }
        if method == "PUT":
            headers["x-amz-acl"] = "private"
            headers["content-type"] = "application/octet-stream"
        headers["authorization"] = authorization(
            method,
            path,
            headers,
            access_key=self.access_key,
            secret_key=self.secret_key,
            region=self.region,
            query=query,
        )
        url = self.base + quote(path, safe="/-_.~") + ("?" + query if query else "")
        built = self.http.build_request(method, url, headers=headers, content=body or None)
        try:
            return self.http.send(built, stream=True)
        except httpx2.HTTPError as error:
            raise BillObjectError("bill object store unavailable") from error

    def fetch(self, path: str, limit: int) -> bytes | None:
        """Return the object body, or None when it does not exist."""
        response = self.request("GET", path)
        try:
            status = response.status_code
            if status == 404:
                return None
            if status != 200:
                raise BillObjectError("bill object store refused the read")
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > limit:
                    break
        except httpx2.HTTPError as error:
            raise BillObjectError("bill object store unavailable") from error
        finally:
            response.close()
        if not 0 < len(body) <= limit:
            raise BillObjectError("invalid bill original")
        return body

    def put(self, owner: UUID, domain: str, body: bytes, suffix: str = ".txt") -> str:
        if suffix not in LIMITS or not body or len(body) > LIMITS[suffix]:
            raise BillObjectError("bounded bill original required")
        digest = hashlib.sha256(body).hexdigest()
        path = self.key(owner, domain, digest, suffix)
        existing = self.fetch(path, LIMITS[suffix])
        if existing is not None:
            if existing != body:
                raise BillObjectError("bill original integrity check failed")
            return digest
        # The signed payload hash makes the store reject a body changed in transit.
        response = self.request("PUT", path, body)
        response.close()
        if response.status_code != 200:
            raise BillObjectError("bill object store refused the write")
        return digest

    def delete(self, owner: UUID, domain: str, digest: str, suffix: str) -> None:
        response = self.request("DELETE", self.key(owner, domain, digest, suffix))
        response.close()
        if response.status_code not in {200, 204, 404}:
            raise BillObjectError("bill object store refused the delete")

    def log_tombstone(self, owner: UUID, domain: str, name: str, body: bytes) -> None:
        """Write one deletion record outside the database, so a restore can reapply it."""
        path = f"/{self.bucket}/{self.prefix}-tombstones/{owner}/{domain}/{name}.json"
        response = self.request("PUT", path, body)
        response.close()
        if response.status_code != 200:
            raise BillObjectError("tombstone log refused the write")

    def tombstones(self) -> list[bytes]:
        """Every logged deletion record. Fails rather than return a partial list."""
        prefix = quote(self.prefix + "-tombstones/", safe="-_.~")
        keys: list[str] = []
        token = ""
        while True:
            # SigV4 signs the query as given, so the parameters stay in sorted order.
            query = f"list-type=2&prefix={prefix}"
            if token:
                query = f"continuation-token={quote(token, safe='-_.~')}&" + query
            response = self.request("GET", f"/{self.bucket}", query=query)
            listing = response.read()
            response.close()
            if response.status_code != 200:
                raise BillObjectError("tombstone log refused the listing")
            root = ElementTree.fromstring(listing)
            ns = {"s3": root.tag.split("}")[0].strip("{")}
            keys += [k.text or "" for k in root.findall("s3:Contents/s3:Key", ns)]
            if root.findtext("s3:IsTruncated", namespaces=ns) != "true":
                break
            token = root.findtext("s3:NextContinuationToken", namespaces=ns) or ""
            if not token:
                raise BillObjectError("tombstone log listing has no continuation token")
        return [self.fetch(f"/{self.bucket}/{key}", 4096) or b"" for key in keys]

    def read(self, owner: UUID, domain: str, digest: str, suffix: str = ".txt") -> bytes:
        path = self.key(owner, domain, digest, suffix)
        body = self.fetch(path, LIMITS[suffix])
        if body is None:
            raise BillObjectError("bill original missing")
        if hashlib.sha256(body).hexdigest() != digest:
            raise BillObjectError("bill original integrity check failed")
        return body

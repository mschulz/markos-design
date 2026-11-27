"""Spaces bill object adapter: SigV4 vector, in-process refusals, and a disposable SeaweedFS S3."""

import hashlib
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2  # type: ignore[import-not-found,unused-ignore]
import pytest

from markos_runtime.knowledge.objects import LIMITS, BillObjectError
from markos_runtime.knowledge.spaces import EMPTY_SHA256, SpacesObjects, authorization

OWNER = uuid4()
BODY = b"Synthetic bill: 412 kWh, total $123.45\n"
DIGEST = hashlib.sha256(BODY).hexdigest()


def test_signature_matches_the_aws_sigv4_example() -> None:
    # "GET Object" example from the AWS S3 SigV4 header-based authentication documentation.
    headers = {
        "host": "examplebucket.s3.amazonaws.com",
        "range": "bytes=0-9",
        "x-amz-content-sha256": EMPTY_SHA256,
        "x-amz-date": "20130524T000000Z",
    }
    value = authorization(
        "GET",
        "/test.txt",
        headers,
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
    )
    assert value.endswith(
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date,"
        " Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def adapter(handler: Callable[[httpx2.Request], httpx2.Response], **extra: Any) -> SpacesObjects:
    return SpacesObjects(
        "https://syd1.example.test",
        "syd1",
        "markos-bills",
        "synthetic-key",
        "synthetic-secret",
        clock=lambda: datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC),
        transport=httpx2.MockTransport(handler),
        **extra,
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://syd1.example.test",
        "https://syd1.example.test/bucket",
        "https://user:pass@syd1.example.test",
        "https://syd1.example.test?x=1",
        "ftp://syd1.example.test",
    ],
)
def test_endpoint_must_be_https_origin(endpoint: str) -> None:
    with pytest.raises(BillObjectError):
        SpacesObjects(endpoint, "syd1", "markos-bills", "k", "s")


@pytest.mark.parametrize(
    ("bucket", "region", "key", "secret"),
    [
        ("Bad_Bucket", "syd1", "k", "s"),
        ("ok-bucket", "SYD 1", "k", "s"),
        ("ok-bucket", "syd1", "", "s"),
    ],
)
def test_settings_are_validated(bucket: str, region: str, key: str, secret: str) -> None:
    with pytest.raises(BillObjectError):
        SpacesObjects("https://syd1.example.test", region, bucket, key, secret)


def test_secret_is_not_in_repr() -> None:
    assert "synthetic-secret" not in repr(adapter(lambda r: httpx2.Response(404)))


def test_requests_are_signed_scoped_and_private() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(404 if request.method == "GET" else 200)

    assert adapter(handler).put(OWNER, "personal", BODY) == DIGEST
    get, put = seen
    assert put.url.path == f"/markos-bills/bills/{OWNER}/personal/{DIGEST}.txt"
    assert put.headers["x-amz-acl"] == "private"
    assert put.headers["x-amz-content-sha256"] == DIGEST
    assert put.headers["authorization"].startswith(
        "AWS4-HMAC-SHA256 Credential=synthetic-key/20300102/syd1/s3/aws4_request,"
    )
    assert get.headers["x-amz-content-sha256"] == EMPTY_SHA256


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (200, BODY + b"changed"),  # wrong content under the hash name
        (200, b""),  # empty
        (200, b"x" * (LIMITS[".txt"] + 1)),  # too large
        (302, b""),  # redirect, never followed
        (403, b""),
        (404, b""),
        (500, b""),
    ],
)
def test_bad_reads_are_refused(status: int, body: bytes) -> None:
    objects = adapter(lambda r: httpx2.Response(status, content=body))
    with pytest.raises(BillObjectError):
        objects.read(OWNER, "personal", DIGEST)


def test_existing_object_with_other_bytes_fails_put() -> None:
    objects = adapter(lambda r: httpx2.Response(200, content=b"other"))
    with pytest.raises(BillObjectError, match="integrity"):
        objects.put(OWNER, "personal", BODY)


def test_network_failure_is_an_object_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("synthetic")

    with pytest.raises(BillObjectError, match="unavailable"):
        adapter(handler).read(OWNER, "personal", DIGEST)


@pytest.mark.parametrize(
    ("owner", "domain", "digest", "suffix"),
    [
        (OWNER, "other", DIGEST, ".txt"),
        (OWNER.__class__(int=0), "personal", DIGEST, ".txt"),
        (OWNER, "personal", "../" + DIGEST[3:], ".txt"),
        (OWNER, "personal", DIGEST, ".exe"),
    ],
)
def test_scope_and_reference_are_validated(
    owner: Any, domain: str, digest: str, suffix: str
) -> None:
    objects = adapter(lambda r: pytest.fail("no request expected"))
    with pytest.raises(BillObjectError):
        objects.read(owner, domain, digest, suffix)


# One bucket per session: SeaweedFS reserves volumes for every new bucket.
@pytest.fixture(scope="session")
def s3() -> Iterator[SpacesObjects]:
    endpoint = os.environ.get("MARKOS_TEST_S3_ENDPOINT")
    if not endpoint:
        if os.environ.get("MARKOS_REQUIRE_S3_TESTS") == "1":
            pytest.fail("set MARKOS_TEST_S3_ENDPOINT to a disposable local S3 store")
        pytest.skip("set MARKOS_TEST_S3_ENDPOINT to a disposable local S3 store")
    if urlsplit(endpoint).hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("the S3 test store must be local and disposable")
    objects = SpacesObjects(
        endpoint,
        "us-east-1",
        "markos-test-" + uuid4().hex[:12],
        os.environ["MARKOS_TEST_S3_ACCESS_KEY"],
        os.environ["MARKOS_TEST_S3_SECRET_KEY"],
    )
    response = objects.request("PUT", "/" + objects.bucket)
    response.close()
    assert response.status_code == 200
    yield objects


def test_store_round_trip_dedup_and_scope(s3: SpacesObjects) -> None:
    owner, other = uuid4(), uuid4()
    assert s3.put(owner, "personal", BODY) == DIGEST
    assert s3.put(owner, "personal", BODY) == DIGEST
    assert s3.read(owner, "personal", DIGEST) == BODY
    pdf = b"%PDF-1.4 synthetic"
    pdf_digest = s3.put(owner, "work", pdf, ".pdf")
    assert s3.read(owner, "work", pdf_digest, ".pdf") == pdf
    for args in [
        (other, "personal", DIGEST),
        (owner, "work", DIGEST),
        (owner, "personal", pdf_digest),
    ]:
        with pytest.raises(BillObjectError, match="missing"):
            s3.read(*args)


def test_store_detects_tampered_original(s3: SpacesObjects) -> None:
    owner = uuid4()
    s3.put(owner, "personal", BODY)
    response = s3.request("PUT", s3.key(owner, "personal", DIGEST, ".txt"), b"tampered")
    response.close()
    assert response.status_code == 200
    with pytest.raises(BillObjectError, match="integrity"):
        s3.read(owner, "personal", DIGEST)
    with pytest.raises(BillObjectError, match="integrity"):
        s3.put(owner, "personal", BODY)


def test_store_refuses_unsigned_and_wrongly_signed_requests(s3: SpacesObjects) -> None:
    owner = uuid4()
    s3.put(owner, "personal", BODY)
    url = s3.base + s3.key(owner, "personal", DIGEST, ".txt")
    assert httpx2.get(url, trust_env=False).status_code == 403
    wrong = SpacesObjects(s3.endpoint, s3.region, s3.bucket, s3.access_key, "wrong-secret")
    with pytest.raises(BillObjectError, match="refused"):
        wrong.read(owner, "personal", DIGEST)

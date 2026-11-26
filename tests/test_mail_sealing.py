"""Gmail token sealing: only the worker's private key opens it, and only for its own mailbox."""

import os
from dataclasses import replace
from uuid import UUID

import pytest

pytest.importorskip("cryptography")

from markos_runtime.mail.sealing import SealError, TokenKey, seal  # noqa: E402

OWNER = UUID("11111111-1111-4111-8111-111111111111")
OTHER = UUID("99999999-9999-4999-8999-999999999999")
SECRET = "1//synthetic-refresh-token"


def test_worker_key_opens_a_token_sealed_with_its_public_key() -> None:
    worker = TokenKey(os.urandom(32))
    sealed = seal(worker.public, OWNER, "personal", SECRET)
    assert sealed.key_id == worker.key_id and len(sealed.key_id) == 32
    assert SECRET.encode() not in sealed.sealed and sealed.sealed[0] == 1
    assert worker.open(sealed) == SECRET
    assert seal(worker.public, OWNER, "personal", SECRET).sealed != sealed.sealed


def test_sealed_token_does_not_open_elsewhere_or_after_tampering() -> None:
    worker = TokenKey(os.urandom(32))
    sealed = seal(worker.public, OWNER, "personal", SECRET)
    flipped = bytearray(sealed.sealed)
    flipped[-1] ^= 1
    for moved in (
        replace(sealed, owner_id=OTHER),
        replace(sealed, domain="work"),
        replace(sealed, sealed=bytes(flipped)),
        replace(sealed, sealed=sealed.sealed[:40]),
        replace(sealed, sealed=b"\x02" + sealed.sealed[1:]),
    ):
        with pytest.raises(SealError):
            worker.open(moved)
    stranger = TokenKey(os.urandom(32))
    with pytest.raises(SealError, match="another key"):
        stranger.open(sealed)
    with pytest.raises(SealError):
        stranger.open(replace(sealed, key_id=stranger.key_id))


def test_bad_inputs_are_refused_without_echoing_secrets() -> None:
    worker = TokenKey(os.urandom(32))
    for token in ("", "x" * 2049):
        with pytest.raises(SealError, match="size"):
            seal(worker.public, OWNER, "personal", token)
    with pytest.raises(SealError, match="public key"):
        seal(b"short", OWNER, "personal", SECRET)
    with pytest.raises(SealError, match="private key"):
        TokenKey(b"short")
    assert repr(worker) == f"TokenKey(key_id={worker.key_id!r})"

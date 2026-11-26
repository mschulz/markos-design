"""Seal a Gmail refresh token so that only the mail worker can open it.

The gateway holds only the worker's public key: it can seal a new token but cannot open a stored
one. Each seal uses a new X25519 key pair, HKDF-SHA256 and AES-256-GCM. The owner and domain are
bound into the key and the tag, so a sealed token copied to another mailbox does not open.

Layout: version byte 1, the 32-byte ephemeral public key, a 12-byte nonce, then ciphertext and tag.
"""

import hashlib
import os
from uuid import UUID

from cryptography.exceptions import InvalidTag  # type: ignore[import-not-found,unused-ignore]
from cryptography.hazmat.primitives import hashes  # type: ignore[import-not-found,unused-ignore]
from cryptography.hazmat.primitives.asymmetric.x25519 import (  # type: ignore[import-not-found,unused-ignore]
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import (  # type: ignore[import-not-found,unused-ignore]
    AESGCM,
)
from cryptography.hazmat.primitives.kdf.hkdf import (  # type: ignore[import-not-found,unused-ignore]
    HKDF,
)

from markos_runtime.mail.gmail import SealedToken

VERSION = 1
MAX_TOKEN_BYTES = 2048
_INFO = b"markos-mail-token-v1"


class SealError(Exception):
    """The token cannot be sealed or opened. The message never contains key or token bytes."""


def key_id(public: bytes) -> str:
    return hashlib.sha256(public).hexdigest()[:32]


def _context(owner_id: UUID, domain: str) -> bytes:
    return f"{owner_id}:{domain}".encode()


def _key(shared: bytes, context: bytes) -> bytes:
    kdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO + b":" + context)
    return bytes(kdf.derive(shared))


def seal(public: bytes, owner_id: UUID, domain: str, token: str) -> SealedToken:
    """Seal with the worker's raw 32-byte public key."""
    plain = token.encode()
    if not 1 <= len(plain) <= MAX_TOKEN_BYTES:
        raise SealError("invalid token size")
    try:
        recipient = X25519PublicKey.from_public_bytes(public)
    except ValueError:
        raise SealError("invalid public key") from None
    ephemeral = X25519PrivateKey.generate()
    mine = ephemeral.public_key().public_bytes_raw()
    context = _context(owner_id, domain)
    nonce = os.urandom(12)
    body = AESGCM(_key(ephemeral.exchange(recipient), context)).encrypt(nonce, plain, context)
    return SealedToken(owner_id, domain, key_id(public), bytes([VERSION]) + mine + nonce + body)


class TokenKey:
    """The mail worker's private key. It never leaves the worker."""

    def __init__(self, private: bytes) -> None:
        try:
            self._private = X25519PrivateKey.from_private_bytes(private)
        except ValueError:
            raise SealError("invalid private key") from None
        self.public = self._private.public_key().public_bytes_raw()
        self.key_id = key_id(self.public)

    def __repr__(self) -> str:
        return f"TokenKey(key_id={self.key_id!r})"

    def open(self, token: SealedToken) -> str:
        data = token.sealed
        if token.key_id != self.key_id:
            raise SealError("token sealed for another key")
        if len(data) < 1 + 32 + 12 + 16 + 1 or data[0] != VERSION:
            raise SealError("invalid sealed token")
        context = _context(token.owner_id, token.domain)
        try:
            shared = self._private.exchange(X25519PublicKey.from_public_bytes(data[1:33]))
            plain = AESGCM(_key(shared, context)).decrypt(data[33:45], data[45:], context)
            return bytes(plain).decode()
        except (ValueError, InvalidTag, UnicodeDecodeError):
            raise SealError("sealed token does not open") from None

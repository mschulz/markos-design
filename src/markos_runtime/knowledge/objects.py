"""Private local object adapter for explicit previews, keyed by scope and content hash."""

import fcntl
import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Protocol
from uuid import UUID

from markos_runtime.knowledge.bills import MAX_TEXT_BYTES
from markos_runtime.knowledge.pdf import MAX_PDF_BYTES

LIMITS = {".txt": MAX_TEXT_BYTES, ".pdf": MAX_PDF_BYTES}


class BillObjectError(ValueError):
    pass


class BillStorageFull(BillObjectError):
    """The private bill folder holds its maximum number of originals."""


class BillStore(Protocol):
    """Where bill originals live: BillObjects (a local folder) or SpacesObjects (a bucket)."""

    def put(self, owner: UUID, domain: str, body: bytes, suffix: str = ".txt") -> str: ...

    def read(self, owner: UUID, domain: str, digest: str, suffix: str = ".txt") -> bytes: ...

    def delete(self, owner: UUID, domain: str, digest: str, suffix: str) -> None:
        """Erase one original. An original that is already gone counts as erased."""
        ...

    def log_tombstone(self, owner: UUID, domain: str, name: str, body: bytes) -> None: ...


class BillObjects:
    def __init__(self, root: Path) -> None:
        if not root.is_absolute() or root.is_symlink():
            raise BillObjectError("explicit private object directory required")
        self.root = root.resolve()
        metadata = self.root.stat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise BillObjectError("private owned object directory required")
        if any((parent / ".git").exists() for parent in (self.root, *self.root.parents)):
            raise BillObjectError("bill originals must stay outside Git")

    def folder(self, owner: UUID, domain: str) -> Path:
        if not owner.int or domain not in {"personal", "research", "work"}:
            raise BillObjectError("valid bill scope required")
        folder = self.root
        for part in (str(owner), domain):
            folder = folder / part
            if folder.is_symlink():
                raise BillObjectError("unsafe object directory")
            folder.mkdir(mode=0o700, exist_ok=True)
        return folder

    def put(self, owner: UUID, domain: str, body: bytes, suffix: str = ".txt") -> str:
        if not body or len(body) > LIMITS[suffix]:
            raise BillObjectError("bounded bill original required")
        digest = hashlib.sha256(body).hexdigest()
        folder = self.folder(owner, domain)
        descriptor = os.open(folder / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = folder / (digest + suffix)
            if path.exists() or path.is_symlink():
                if self.read(owner, domain, digest, suffix) != body:
                    raise BillObjectError("bill original integrity check failed")
                return digest
            if sum(1 for p in folder.iterdir() if p.suffix in LIMITS) >= 100:
                raise BillStorageFull("preview bill storage limit reached")
            with tempfile.NamedTemporaryFile(dir=folder, prefix=".writing-", delete=False) as file:
                temporary = Path(file.name)
                try:
                    file.write(body)
                    file.flush()
                    os.fsync(file.fileno())
                    os.link(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
        return digest

    def delete(self, owner: UUID, domain: str, digest: str, suffix: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or suffix not in LIMITS:
            raise BillObjectError("invalid bill source reference")
        folder = self.folder(owner, domain)
        descriptor = os.open(folder / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            (folder / (digest + suffix)).unlink(missing_ok=True)
            # The removal is durable only when the folder entry is flushed.
            handle = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(handle)
            finally:
                os.close(handle)

    def log_tombstone(self, owner: UUID, domain: str, name: str, body: bytes) -> None:
        """Write one deletion record outside the database, so a restore can reapply it."""
        folder = self.root / "tombstones" / str(owner) / domain
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        (folder / (name + ".json")).write_bytes(body)

    def tombstones(self) -> list[bytes]:
        """Every logged deletion record."""
        return [p.read_bytes() for p in sorted((self.root / "tombstones").glob("*/*/*.json"))]

    def read(self, owner: UUID, domain: str, digest: str, suffix: str = ".txt") -> bytes:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise BillObjectError("invalid bill source reference")
        descriptor = os.open(
            self.folder(owner, domain) / (digest + suffix), os.O_RDONLY | os.O_NOFOLLOW
        )
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= LIMITS[suffix]:
                raise BillObjectError("invalid bill original")
            body = source.read(LIMITS[suffix] + 1)
        if hashlib.sha256(body).hexdigest() != digest:
            raise BillObjectError("bill original integrity check failed")
        return body

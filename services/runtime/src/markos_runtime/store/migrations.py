"""Explicit migration-role API; no discovery, worker startup or runtime grants."""

import re
from hashlib import sha256
from pathlib import Path

import psycopg

from markos_runtime.store.database import Row, StorageError


async def migrate(connection: psycopg.AsyncConnection[Row], directory: Path) -> None:
    try:
        files = sorted(directory.glob("*.sql"))
        if not files or any(not re.fullmatch(r"[0-9]{4}_[a-z_]+\.sql", p.name) for p in files):
            raise StorageError("invalid migration set")
        versions = [int(path.name[:4]) for path in files]
        if versions != list(range(1, len(files) + 1)):
            raise StorageError("non-contiguous migration set")
        async with connection.transaction():
            await connection.execute("SET LOCAL lock_timeout='5s'")
            await connection.execute("SET LOCAL statement_timeout='30s'")
            # Database-local lock, serializing independent migration processes.
            await connection.execute("SELECT pg_advisory_xact_lock(712093481)")
            await connection.execute("CREATE SCHEMA IF NOT EXISTS markos_runtime")
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS markos_runtime.schema_migrations "
                "(version integer PRIMARY KEY, sha256 text NOT NULL, "
                "applied_at timestamptz NOT NULL DEFAULT clock_timestamp())"
            )
            cursor = await connection.execute(
                "SELECT version,sha256 FROM markos_runtime.schema_migrations"
            )
            applied = {row["version"]: row["sha256"] for row in await cursor.fetchall()}
            if set(applied) - set(versions):
                raise StorageError("unknown applied migration")
            for version, path in zip(versions, files, strict=True):
                body = path.read_bytes()
                digest = sha256(body).hexdigest()
                if version in applied:
                    if applied[version] != digest:
                        raise StorageError("migration checksum mismatch")
                    continue
                await connection.execute(body.decode("utf-8"), prepare=False)
                await connection.execute(
                    "INSERT INTO markos_runtime.schema_migrations(version,sha256) VALUES(%s,%s)",
                    (version, digest),
                )
    except (psycopg.Error, OSError, UnicodeError):
        raise StorageError("migration failed") from None

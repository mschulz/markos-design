"""Opt-in synthetic PostgreSQL only; creates/drops private test databases."""

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from markos_runtime.store.migrations import migrate

OWNER = "11111111-1111-4111-8111-111111111111"
OTHER = "99999999-9999-4999-8999-999999999999"
ROLES = {
    "publisher": ("m0b_pub", "foundation-generator", OWNER, "personal", True, False),
    "consumer": ("m0b_cons", "foundation-worker", OWNER, "personal", False, True),
    "audit": ("m0b_audit", "foundation-audit", OWNER, "personal", False, True),
    "workpub": ("m0b_workpub", "work-generator", OWNER, "work", True, False),
    "workcons": ("m0b_workcons", "work-worker", OWNER, "work", False, True),
    "otherpub": ("m0b_otherpub", "other-generator", OTHER, "personal", True, False),
    "othercons": ("m0b_othercons", "other-worker", OTHER, "personal", False, True),
}


@pytest.fixture(scope="session")
def admin_dsn() -> str:
    dsn = os.environ.get("MARKOS_TEST_ADMIN_DSN")
    if not dsn:
        if os.environ.get("MARKOS_REQUIRE_DB_TESTS") == "1":
            pytest.fail("required synthetic PostgreSQL tests have no DSN")
        pytest.skip("set MARKOS_TEST_ADMIN_DSN for isolated PostgreSQL integration")
    info = conninfo_to_dict(dsn)
    host = str(info.get("host", ""))
    if not (host in {"127.0.0.1", "localhost"} or host.startswith("/private/tmp/markos-m0b-")):
        pytest.fail("synthetic PostgreSQL tests require explicit local test host")
    with psycopg.connect(dsn, autocommit=True) as connection:
        for name in ["markos_migrator", "markos_runtime_client", "m0b_unmapped"] + [
            role[0] for role in ROLES.values()
        ]:
            if connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone():
                continue
            login = "NOLOGIN" if name == "markos_runtime_client" else "LOGIN"
            connection.execute(
                sql.SQL(
                    "CREATE ROLE {} {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS "
                    "PASSWORD 'synthetic-test-only'"
                ).format(sql.Identifier(name), sql.SQL(login))
            )
        for name in ["m0b_unmapped"] + [r[0] for r in ROLES.values()]:
            connection.execute(
                sql.SQL("GRANT markos_runtime_client TO {}").format(sql.Identifier(name))
            )
    return dsn


@pytest.fixture
def database(admin_dsn: str) -> Iterator[dict[str, str]]:
    name = "markos_test_" + uuid4().hex
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER markos_migrator").format(sql.Identifier(name))
        )
    dsns = {
        key: make_conninfo(admin_dsn, dbname=name, user=role[0], password="synthetic-test-only")
        for key, role in ROLES.items()
    }
    dsns["migration"] = make_conninfo(
        admin_dsn, dbname=name, user="markos_migrator", password="synthetic-test-only"
    )
    dsns["admin"] = make_conninfo(admin_dsn, dbname=name)
    dsns["unmapped"] = make_conninfo(
        admin_dsn, dbname=name, user="m0b_unmapped", password="synthetic-test-only"
    )

    async def setup() -> None:
        async with await psycopg.AsyncConnection.connect(
            dsns["migration"], autocommit=True, row_factory=dict_row
        ) as connection:
            await migrate(
                connection, Path(__file__).resolve().parents[4] / "infrastructure/migrations"
            )
            for role in ROLES.values():
                await connection.execute(
                    "INSERT INTO markos_runtime.service_identities "
                    "(login_role,service_id,owner_id,domain,can_publish,can_consume) "
                    "VALUES(%s,%s,%s,%s,%s,%s)",
                    role,
                )

    try:
        asyncio.run(setup())
        yield dsns
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))

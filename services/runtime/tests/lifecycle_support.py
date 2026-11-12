"""Owned GitHub-hosted synthetic harness; never imported by the runtime image."""

import argparse
import asyncio
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from markos_runtime.contracts.events import EventEnvelope
from markos_runtime.contracts.schedules import ScheduleDefinition
from markos_runtime.execution.scheduler import Scheduler
from markos_runtime.recovery.service import Recovery
from markos_runtime.store.database import DurableStore
from markos_runtime.store.migrations import migrate
from tests.integration.test_rabbitmq import Harness
from tests.integration.test_storage import event

ROOT = Path(__file__).resolve().parents[3]
APPS = [
    "publisher",
    "consumer",
    "worker",
    "review",
    "audit-consumer",
    "audit-worker",
    "audit-review",
    "scheduler",
    "recovery",
]
OWNER = "11111111-1111-4111-8111-111111111111"
SCHEDULE = UUID("55555555-5555-4555-8555-555555555555")
SOURCES = (
    UUID("66666666-6666-4666-8666-666666666666"),
    UUID("77777777-7777-4777-8777-777777777777"),
)
SOURCE_BODY = b"M0-F synthetic source only"
LOGINS = {"publisher": "m0b_pub", "consumer": "m0b_cons", "audit": "m0b_audit"}


def project() -> str:
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("MARKOS_REQUIRE_LIFECYCLE_TEST") != "1"
        or os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
        != "unix:///var/run/docker.sock"
    ):
        raise RuntimeError("isolated GitHub-hosted synthetic lifecycle job required")
    value = os.environ.get("MARKOS_TEST_COMPOSE_PROJECT", "")
    if not re.fullmatch(r"markos-m0f-[0-9]+-[0-9]+", value):
        raise RuntimeError("explicit owned lifecycle project required")
    return value


def command(args: list[str], *, body: bytes | None = None, timeout: int = 120) -> bytes:
    result = subprocess.run(args, input=body, capture_output=True, timeout=timeout)
    if result.returncode:
        # These are synthetic test-only diagnostics, not runtime logging.
        raise RuntimeError(
            f"harness command failed: {args[:3]}: " + result.stderr.decode(errors="replace")[-2000:]
        )
    return result.stdout


def compose(
    *args: str, override: Path | None = None, body: bytes | None = None, timeout: int = 120
) -> bytes:
    prefix = [
        "docker",
        "compose",
        "-p",
        project(),
        "-f",
        str(ROOT / "infrastructure/compose.m0-test.yml"),
    ]
    if override is not None:
        prefix += ["-f", str(override)]
    return command(prefix + list(args), body=body, timeout=timeout)


def container(service: str) -> str:
    ident = compose("ps", "-a", "-q", service).decode().strip()
    if not re.fullmatch("[0-9a-f]{12,64}", ident):
        raise RuntimeError("owned service container required")
    meta = json.loads(command(["docker", "inspect", ident]))[0]
    if meta["Config"]["Labels"]["com.docker.compose.project"] != project():
        raise RuntimeError("foreign container")
    return ident


def admin(database: str = "markos_m0f") -> str:
    return make_conninfo(
        host="127.0.0.1",
        port=5432,
        dbname=database,
        user="postgres",
        password="synthetic-test-only",
    )


def dsn(role: str, database: str = "markos_m0f") -> str:
    return make_conninfo(admin(database), user=LOGINS[role], password="synthetic-test-only")


def wait(check: Any, seconds: float = 90) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (OSError, RuntimeError, psycopg.Error):
            pass
        time.sleep(0.2)
    raise AssertionError("synthetic lifecycle condition timed out")


def ready(service: str) -> bool:
    try:
        rows = compose("ps", "--format", "json", service).decode().strip()
        values = (
            json.loads(rows)
            if rows.startswith("[")
            else [json.loads(line) for line in rows.splitlines()]
        )
        return bool(values) and all(
            v.get("Health") == "healthy" and v.get("State") == "running" for v in values
        )
    except (ValueError, RuntimeError):
        return False


def health(service: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(
            compose(
                "exec",
                "-T",
                service,
                "/opt/runtime/.venv/bin/python",
                "-c",
                'import urllib.request;print(urllib.request.urlopen("http://127.0.0.1:8787/metrics",timeout=1).read().decode())',
            )
        ),
    )


def certs() -> None:
    project()
    folder = ROOT / "infrastructure/.test-certs"
    folder.mkdir(exist_ok=True)
    command(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=markos-synthetic-ca",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-keyout",
            str(folder / "ca.key"),
            "-out",
            str(folder / "ca.pem"),
        ]
    )
    command(
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=rabbitmq",
            "-keyout",
            str(folder / "server.key"),
            "-out",
            str(folder / "server.csr"),
        ]
    )
    (folder / "extensions.cnf").write_text(
        "subjectAltName=DNS:rabbitmq,DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n"
    )
    command(
        [
            "openssl",
            "x509",
            "-req",
            "-days",
            "1",
            "-in",
            str(folder / "server.csr"),
            "-CA",
            str(folder / "ca.pem"),
            "-CAkey",
            str(folder / "ca.key"),
            "-CAcreateserial",
            "-extfile",
            str(folder / "extensions.cnf"),
            "-out",
            str(folder / "server.pem"),
        ]
    )
    for path in folder.iterdir():
        path.chmod(0o644)  # Public disposable fixture keys, never production material.


async def bootstrap() -> None:
    project()
    with psycopg.connect(admin("postgres"), autocommit=True) as conn:
        for name in ["markos_migrator", "markos_runtime_client", *LOGINS.values()]:
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone():
                continue
            conn.execute(
                sql.SQL(
                    "CREATE ROLE {} {} NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOBYPASSRLS PASSWORD 'synthetic-test-only'"
                ).format(
                    sql.Identifier(name),
                    sql.SQL("NOLOGIN" if name == "markos_runtime_client" else "LOGIN"),
                )
            )
        for name in LOGINS.values():
            conn.execute(sql.SQL("GRANT markos_runtime_client TO {}").format(sql.Identifier(name)))
        conn.execute("CREATE DATABASE markos_m0f OWNER markos_migrator")
    migration = make_conninfo(admin(), user="markos_migrator")
    async with await psycopg.AsyncConnection.connect(
        migration, autocommit=True, row_factory=dict_row
    ) as conn:
        await migrate(conn, ROOT / "infrastructure/migrations")
        for role, service, publish, consume in [
            ("publisher", "foundation-generator", True, False),
            ("consumer", "foundation-worker", False, True),
            ("audit", "foundation-audit", False, True),
        ]:
            await conn.execute(
                "INSERT INTO markos_runtime.service_identities "
                "(login_role,service_id,owner_id,domain,can_publish,can_consume,"
                "can_execute,can_schedule,can_replay,can_manage_retention) "
                "VALUES(%s,%s,%s,'personal',%s,%s,%s,%s,%s,%s)",
                (
                    LOGINS[role],
                    service,
                    OWNER,
                    publish,
                    consume,
                    consume,
                    publish,
                    publish,
                    publish,
                ),
            )
    broker = Harness("http://127.0.0.1:15672", "m0c-admin", "synthetic-test-only")
    broker.vhost = "markos-test"
    broker.users = {k: "syn-" + k for k in broker.users}
    broker.setup()
    async with DurableStore.connect(dsn("publisher")) as pub:
        await Scheduler(pub).register(
            ScheduleDefinition(
                schema_version=1,
                schedule_id=SCHEDULE,
                version=1,
                owner_id=UUID(OWNER),
                domain="personal",
                kind="interval",
                start_at=datetime.now(UTC) - timedelta(seconds=61),
                timezone="Australia/Brisbane",
                interval_seconds=60,
                missed_policy="catch_up",
            )
        )
        for source in SOURCES:
            await Recovery(pub).observe_synthetic_source(
                source,
                SOURCE_BODY,
                ("foundation-generator", "foundation-worker", "foundation-audit"),
            )
        await Recovery(pub).delete_source(SOURCES[1])


def emit(label: str, database: str = "markos_m0f") -> EventEnvelope:
    item = event(label=label, operation="m0f:" + label + ":" + uuid4().hex)

    async def append() -> None:
        async with DurableStore.connect(dsn("publisher", database)) as pub:
            await pub.append(item)

    asyncio.run(append())
    return item


def results(eid: UUID, database: str = "markos_m0f") -> int:
    with psycopg.connect(admin(database)) as conn:
        row = conn.execute(
            "SELECT count(*) FROM markos_runtime.tasks t JOIN "
            "markos_runtime.task_results r USING(owner_id,domain,consumer_id,task_id) "
            "WHERE t.event_id=%s",
            (eid,),
        ).fetchone()
        assert row is not None
        return int(row[0])


def running(eid: UUID) -> bool:
    with psycopg.connect(admin()) as conn:
        return (
            conn.execute(
                "SELECT 1 FROM markos_runtime.tasks WHERE event_id=%s AND "
                "consumer_id='foundation-worker' AND state='running'",
                (eid,),
            ).fetchone()
            is not None
        )


def phase(code: str) -> None:
    print("LIFECYCLE_PHASE " + code, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["certs", "bootstrap"])
    args = parser.parse_args()
    if args.action == "certs":
        certs()
    else:
        asyncio.run(bootstrap())

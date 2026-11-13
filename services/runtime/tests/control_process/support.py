"""Explicit owned local/hosted preview controller. Never shipped in the runtime image."""

import argparse
import asyncio
import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from markos_runtime.api.identity import opaque
from markos_runtime.control.store import ControlStore
from markos_runtime.control_lifecycle.config import ControlServiceConfig
from markos_runtime.store.migrations import migrate
from tests.integration.test_control_persistence import prepare, session
from tests.integration.test_rabbitmq import Harness

ROOT = Path(__file__).resolve().parents[4]
FOLDER = ROOT / "infrastructure/.m1g-preview"
CERTS = ROOT / "infrastructure/.test-certs"
APPS = [
    "gateway",
    "issuer",
    "mcp",
    "publisher",
    "consumer",
    "worker",
    "worker-two",
    "completion-publisher",
    "activity",
]
OWNER = "11111111-1111-4111-8111-111111111111"
ORIGIN = "https://127.0.0.1:18443"


def project() -> str:
    name = os.environ.get("MARKOS_TEST_COMPOSE_PROJECT", "")
    if not re.fullmatch(r"markos-m1g-(?:[0-9]+-[0-9]+|local-[a-f0-9]{12})", name):
        raise RuntimeError("explicit owned synthetic project required")
    if os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock") not in {
        "unix:///var/run/docker.sock",
        "unix://" + str(Path.home() / ".docker/run/docker.sock"),
    }:
        raise RuntimeError("local Docker only")
    # A named cloud Docker context must never inherit this controller's destructive scope.
    context = json.loads(command(["docker", "context", "inspect"]))[0]
    host = context["Endpoints"]["docker"]["Host"]
    if not host.startswith("unix://") or os.environ.get("DOCKER_CONTEXT") not in {
        None,
        "default",
        "desktop-linux",
    }:
        raise RuntimeError("local Docker context required")
    if (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise RuntimeError("disposable hosted runner required")
    return name


def command(args: list[str], *, body: bytes | None = None, timeout: int = 180) -> bytes:
    result = subprocess.run(args, input=body, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(
            "synthetic controller command failed: "
            + " ".join(args[:3])
            + ": "
            + result.stderr.decode(errors="replace")[-800:]
        )
    return result.stdout


def compose(*args: str, body: bytes | None = None, timeout: int = 180) -> bytes:
    return command(
        [
            "docker",
            "compose",
            "-p",
            project(),
            "-f",
            str(ROOT / "infrastructure/compose.m1-control-test.yml"),
            *args,
        ],
        body=body,
        timeout=timeout,
    )


def container(role: str) -> str:
    if role not in APPS + ["postgres", "rabbitmq"]:
        raise ValueError("fixed role required")
    ident = compose("ps", "-a", "-q", role).decode().strip()
    if not re.fullmatch(r"[a-f0-9]{12,64}", ident):
        raise RuntimeError("owned container required")
    meta = json.loads(command(["docker", "inspect", ident]))[0]
    if meta["Config"]["Labels"]["com.docker.compose.project"] != project():
        raise RuntimeError("foreign container")
    return ident


def admin() -> str:
    return "host=127.0.0.1 port=5432 dbname=markos_m1g user=postgres password=synthetic-test-only"


def data() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((FOLDER / "controller.json").read_text()))


def write_private(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    path.chmod(0o600)


def certificates() -> None:
    # Public disposable test TLS key, not an identity/provider credential.
    CERTS.mkdir(exist_ok=True)
    command(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "2",
            "-subj",
            "/CN=synthetic-preview",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "subjectAltName=DNS:rabbitmq,DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(CERTS / "server.key"),
            "-out",
            str(CERTS / "server.pem"),
        ]
    )
    (CERTS / "ca.pem").write_bytes((CERTS / "server.pem").read_bytes())
    for name in ("ca.pem", "server.pem", "server.key"):
        (CERTS / name).chmod(0o644)


def prepare_files() -> None:
    project()
    if FOLDER.exists():
        raise RuntimeError(
            "existing preview preserved; use its recorded project or choose a fresh checkout"
        )
    FOLDER.mkdir(mode=0o700)
    (FOLDER / "config").mkdir(mode=0o755)
    credentials = {name: opaque() for name in ("both", "approver", "viewer")}
    tokens = {name: opaque() for name in ("api", "worker", "issuer_admin", "tool", "tool_admin")}
    state = {
        "project": project(),
        "credentials": credentials,
        "tokens": tokens,
        "session_key": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    }
    write_private(FOLDER / "controller.json", state)
    certificates()
    for role in ("issuer", "mcp"):
        directory = FOLDER / (role + "-state")
        directory.mkdir(mode=0o700)
        write_private(
            directory / "seed.json",
            {
                "schema_version": 1,
                "accounts": [
                    {"subject": name, "credential": credential}
                    for name, credential in credentials.items()
                ],
            },
        )
    # Placeholder per-role env files allow Compose to parse before privileged bootstrap.
    for role in APPS:
        (FOLDER / (role + ".env")).write_text("")


async def bootstrap() -> None:
    project()
    state = data()
    # Only the new project's private PostgreSQL service is reachable here.
    with psycopg.connect(make_conninfo(admin(), dbname="postgres"), autocommit=True) as conn:
        for role in (
            "markos_migrator",
            "markos_runtime_client",
            "markos_control_gateway",
            "m1g_gateway",
            "m1g_pub",
            "m1g_worker",
            "m1g_activity",
        ):
            conn.execute(
                sql.SQL(
                    "CREATE ROLE {} {} NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOBYPASSRLS PASSWORD 'synthetic-test-only'"
                ).format(
                    sql.Identifier(role),
                    sql.SQL(
                        "NOLOGIN"
                        if role in {"markos_runtime_client", "markos_control_gateway"}
                        else "LOGIN"
                    ),
                )
            )
        conn.execute("GRANT markos_control_gateway TO m1g_gateway")
        conn.execute("GRANT markos_runtime_client TO m1g_pub,m1g_worker,m1g_activity")
        conn.execute("CREATE DATABASE markos_m1g OWNER markos_migrator")
    migration = make_conninfo(admin(), user="markos_migrator")
    async with await psycopg.AsyncConnection.connect(
        migration, autocommit=True, row_factory=dict_row
    ) as conn:
        await migrate(conn, ROOT / "infrastructure/migrations")
        await conn.execute("INSERT INTO markos_control.gateway_logins VALUES('m1g_gateway')")
        principals = {name: str(uuid4()) for name in state["credentials"]}
        for name, principal in principals.items():
            await conn.execute(
                "INSERT INTO markos_control.principals VALUES(%s,'synthetic-issuer',%s,false)",
                (principal, name),
            )
            roles = ["administrator", "operator"] if name == "both" else [name]
            await conn.execute(
                "INSERT INTO markos_control.memberships VALUES(%s,%s,'personal',1,%s)",
                (principal, OWNER, roles),
            )
        for login, service, pub, consume, execute in [
            ("m1g_pub", "control-gateway", True, False, False),
            ("m1g_worker", "control-test-agent", True, True, True),
            ("m1g_activity", "control-activity", False, True, False),
        ]:
            await conn.execute(
                "INSERT INTO markos_runtime.service_identities("
                "login_role,service_id,owner_id,domain,"
                "can_publish,can_consume,can_execute) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (login, service, OWNER, "personal", pub, consume, execute),
            )
    async with ControlStore.connect(make_conninfo(admin(), user="m1g_gateway")) as store:
        ctx = await session(store)
        state["conversation_id"] = str(await prepare(store, ctx))
        from markos_runtime.contracts.governance import ReviewedFixtureGrant

        grant = ReviewedFixtureGrant(
            schema_version=1,
            owner_id=UUID(OWNER),
            domain="personal",
            grant_id=uuid4(),
            revision=1,
            previous_revision=0,
            endpoint="https://127.0.0.1:19443/mcp",
            require_approval=True,
            enabled=True,
            reason="Explicit synthetic preview",
        )
        await store._call(
            "save_tool_grant", ctx.session_id, ctx.lookup_hash, Jsonb(grant.model_dump(mode="json"))
        )
    rabbit = Harness("http://127.0.0.1:15672", "m0c-admin", "synthetic-test-only")
    rabbit.setup()
    definitions = json.loads(
        (ROOT / "infrastructure/rabbitmq/definitions.control.example.json").read_text()
    )
    for queue in definitions["queues"]:
        rabbit.queue(queue["name"])
    for binding in definitions["bindings"]:
        rabbit.bind(binding["source"], binding["destination"], binding["routing_key"])
    for role, read, write in [
        ("chatpub", r"^$", r"^v1\.control\.chat-requested$"),
        ("chatworker", r"^markos\.personal\.control-test-agent$", r"^v1\.control\.chat-completed$"),
        ("chatactivity", r"^markos\.personal\.control-activity$", r"^$"),
    ]:
        user = "preview-" + role
        rabbit.users[role] = user
        rabbit.request("PUT", "users", user, data={"password": "synthetic-test-only", "tags": ""})
        rabbit.request(
            "PUT",
            "permissions",
            rabbit.vhost,
            user,
            data={
                "configure": r"^$",
                "write": r"^markos\.personal\.events$" if role != "chatactivity" else r"^$",
                "read": read,
            },
        )
        rabbit.request(
            "PUT",
            "topic-permissions",
            rabbit.vhost,
            user,
            data={"exchange": "markos.personal.events", "write": write, "read": r".*"},
        )
    state["principals"] = principals
    for i, role in enumerate(APPS):
        component = "worker" if role == "worker-two" else role
        service = (
            "control-gateway"
            if component in {"gateway", "issuer", "publisher"}
            else "control-activity"
            if component == "activity"
            else "control-test-agent"
        )
        config = ControlServiceConfig(
            schema_version=1,
            profile="control-process-fixture-v1",
            component=cast(Any, component),
            service_id=cast(Any, service),
            owner_id=UUID(OWNER),
            domain="personal",
            health_port=8780 + i,
            listen_host="0.0.0.0",
            ca_file="/etc/test-ca/ca.pem",
            certificate_file="/etc/test-ca/server.pem",
            private_key_file="/etc/test-ca/server.key",
            fixture_state_file="/fixture/state.json",
            fixture_seed_file="/fixture/seed.json",
            broker_publisher_user=rabbit.users[
                "chatworker" if component == "activity" else "chatpub"
            ],
        )
        (FOLDER / "config" / (role + ".json")).write_bytes(config.to_wire())
        env = {"SSL_CERT_FILE": "/etc/test-ca/ca.pem"}
        if component not in {"issuer", "mcp"}:
            login = (
                "m1g_gateway"
                if component == "gateway"
                else "m1g_pub"
                if component == "publisher"
                else "m1g_activity"
                if component == "activity"
                else "m1g_worker"
            )
            env["MARKOS_CONTROL_DATABASE_DSN"] = make_conninfo(admin(), host="postgres", user=login)
        if component in {"publisher", "consumer", "completion-publisher", "activity"}:
            broker_role = (
                "chatpub"
                if component == "publisher"
                else "chatactivity"
                if component == "activity"
                else "chatworker"
            )
            env["MARKOS_CONTROL_BROKER_DSN"] = (
                f"amqps://{rabbit.users[broker_role]}:synthetic-test-only@rabbitmq:5671/{rabbit.vhost}"
            )
            if component in {"publisher", "completion-publisher"}:
                env.update(
                    MARKOS_CONTROL_AUDIT_USER=rabbit.users["observer"],
                    MARKOS_CONTROL_AUDIT_PASSWORD="synthetic-test-only",
                )
        if component in {"issuer", "gateway"}:
            env["MARKOS_CONTROL_ISSUER_API_TOKEN"] = state["tokens"]["api"]
        if component in {"issuer", "worker"}:
            env["MARKOS_CONTROL_ISSUER_WORKER_TOKEN"] = state["tokens"]["worker"]
        if component == "issuer":
            env["MARKOS_CONTROL_ISSUER_ADMIN_TOKEN"] = state["tokens"]["issuer_admin"]
        if component in {"mcp", "worker"}:
            env["MARKOS_CONTROL_TOOL_TOKEN"] = state["tokens"]["tool"]
        if component == "mcp":
            env["MARKOS_CONTROL_FIXTURE_ADMIN_TOKEN"] = state["tokens"]["tool_admin"]
        if component == "gateway":
            env["MARKOS_CONTROL_SESSION_KEY"] = state["session_key"]
        file = FOLDER / (role + ".env")
        file.write_text("".join(key + "=" + value + "\n" for key, value in env.items()))
        file.chmod(0o600)
    write_private(FOLDER / "controller.json", state)


def wait_healthy(seconds: int = 120) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ids = [container(role) for role in APPS]
        metadata = json.loads(command(["docker", "inspect", *ids]))
        if all(x["State"].get("Health", {}).get("Status") == "healthy" for x in metadata):
            return
        time.sleep(1)
    raise RuntimeError("synthetic preview readiness deadline exceeded")


def main() -> None:
    parser = argparse.ArgumentParser(description="Owned synthetic preview only; no deployment")
    parser.add_argument("action", choices=["prepare", "bootstrap", "start", "stop"])
    parser.add_argument("--confirm-synthetic", action="store_true", required=True)
    args = parser.parse_args()
    project()
    if args.action == "prepare":
        prepare_files()
    elif args.action == "bootstrap":
        asyncio.run(bootstrap())
    elif args.action == "start":
        for role in ("issuer", "mcp"):
            compose(
                "run",
                "--rm",
                "--no-deps",
                "--user",
                "0:0",
                "--entrypoint",
                "/opt/runtime/.venv/bin/python",
                role,
                "-c",
                "import os;os.chown('/fixture',10001,10001);os.chmod('/fixture',0o700);"
                "os.chown('/fixture/seed.json',10001,10001)",
            )
        compose("up", "-d", "--no-build", *APPS)
        wait_healthy()
        print(
            "Synthetic preview ready at "
            + ORIGIN
            + "/app; use the local private controller file for fixture login."
        )
    else:
        for role in APPS + ["postgres", "rabbitmq"]:
            container(role)
        compose("down", "-v", "--remove-orphans")


if __name__ == "__main__":
    main()

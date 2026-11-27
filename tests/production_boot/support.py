"""CI-only controller: boots compose.production.yml against disposable stand-ins.

Never shipped in the runtime image. It writes synthetic secrets and certificates under
infrastructure/.m7-production and drives only its own Compose project.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import secrets as tokens
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from markos_runtime.control_lifecycle.production import ProductionServiceConfig
from markos_runtime.store.migrations import migrate

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "infrastructure/.m7-production"
ETC = FOLDER / "etc"
OWNER = "11111111-1111-4111-8111-111111111111"
DOMAIN = "markos.markos-ci.test"
DATABASE_HOST = "db.markos-ci.test"
VHOST = "markos"
PASSWORD = "synthetic-test-only"
# Component, service identity and database login, in Compose service order.
SERVICES = {
    "gateway": ("control-gateway", "m7_gateway"),
    "issuer": ("control-gateway", "m7_issuer"),
    "publisher": ("control-gateway", "m7_pub"),
    "completion-publisher": ("control-test-agent", "m7_worker"),
    "consumer": ("control-test-agent", "m7_worker"),
    "activity": ("control-activity", "m7_activity"),
    "worker": ("control-test-agent", "m7_worker"),
}
BROKER_USERS = {"publisher": "chatpub", "activity": "chatactivity"}
CERT_DAYS = "2"


def project() -> str:
    name = os.environ.get("MARKOS_TEST_COMPOSE_PROJECT", "")
    if not re.fullmatch(r"markos-m7-(?:[0-9]+-[0-9]+|local-[a-f0-9]{12})", name):
        raise RuntimeError("explicit owned CI project required")
    if os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock") not in {
        "unix:///var/run/docker.sock",
        "unix://" + str(Path.home() / ".docker/run/docker.sock"),
    }:
        raise RuntimeError("local Docker only")
    if (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise RuntimeError("disposable hosted runner required")
    return name


def port(name: str) -> int:
    raw = os.environ.get(name, "")
    if not re.fullmatch(r"[1-9][0-9]{3,4}", raw) or not 1024 <= int(raw) <= 65535:
        raise ValueError("explicit bounded loopback port required")
    if int(raw) in {5432, 15432, 25432}:
        raise ValueError("a preview or host PostgreSQL port is never a CI stand-in")
    return int(raw)


def command(args: list[str], *, timeout: int = 300) -> bytes:
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(
            "CI controller command failed: "
            + " ".join(args[:4])
            + ": "
            + result.stderr.decode(errors="replace")[-1500:]
        )
    return result.stdout


def compose(*args: str, timeout: int = 300) -> bytes:
    env = {
        "MARKOS_IMAGE_TAG": project(),
        "MARKOS_DOMAIN": DOMAIN,
        "MARKOS_ETC": str(ETC),
        "MARKOS_CI_POSTGRES_PORT": str(port("MARKOS_CI_POSTGRES_PORT")),
        "MARKOS_CI_HTTPS_PORT": str(port("MARKOS_CI_HTTPS_PORT")),
    }
    os.environ.update(env)
    # Hosted runners have the Compose plugin. Some local installs have only the binary.
    binary = ["docker", "compose"]
    if subprocess.run([*binary, "version"], capture_output=True).returncode:
        binary = ["docker-compose"]
    return command(
        [
            *binary,
            "-p",
            project(),
            "-f",
            str(ROOT / "infrastructure/compose.production.yml"),
            "-f",
            str(ROOT / "infrastructure/compose.production-ci.yml"),
            *args,
        ],
        timeout=timeout,
    )


def certificate(directory: Path, name: str, ca: str, names: list[str]) -> None:
    """A leaf certificate signed by `ca`, with the extensions strict X.509 checks need."""
    key, csr, ext = (directory / (name + s) for s in (".key", ".csr", ".ext"))
    command(
        ["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=" + names[0]]
        + ["-keyout", str(key), "-out", str(csr)]
    )
    ext.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n"
        "subjectAltName=" + ",".join("DNS:" + n for n in names) + "\n"
    )
    command(
        ["openssl", "x509", "-req", "-in", str(csr), "-days", CERT_DAYS, "-sha256"]
        + ["-CA", str(directory / (ca + ".pem")), "-CAkey", str(directory / (ca + ".key"))]
        + ["-CAcreateserial", "-extfile", str(ext), "-out", str(directory / (name + ".pem"))]
    )
    csr.unlink()
    ext.unlink()


def authority(directory: Path, name: str) -> None:
    command(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", CERT_DAYS]
        + ["-subj", "/CN=" + name, "-addext", "basicConstraints=critical,CA:TRUE"]
        + ["-addext", "keyUsage=critical,keyCertSign,cRLSign"]
        + ["-keyout", str(directory / (name + ".key")), "-out", str(directory / (name + ".pem"))]
    )


def rabbit_hash(password: str) -> str:
    salt = os.urandom(4)
    return base64.b64encode(salt + hashlib.sha256(salt + password.encode()).digest()).decode()


def definitions(users: dict[str, str]) -> dict[str, Any]:
    """Users, permissions and the reviewed control topology, loaded at broker boot."""
    topology = json.loads(
        (ROOT / "infrastructure/rabbitmq/definitions.control.example.json").read_text()
    )
    for kind in ("exchanges", "queues", "bindings"):
        for item in topology[kind]:
            item["vhost"] = VHOST
    read = {
        "chatpub": r"^$",
        "chatworker": r"^markos\.personal\.control-test-agent$",
        "chatactivity": r"^markos\.personal\.control-activity$",
        "observer": r"^$",
    }
    write = {
        "chatpub": r"^v1\.control\.chat-requested$",
        "chatworker": r"^v1\.control\.chat-completed$",
        "chatactivity": r"^$",
        "observer": r"^$",
    }
    return {
        "users": [
            {
                "name": name,
                "password_hash": rabbit_hash(password),
                "hashing_algorithm": "rabbit_password_hashing_sha256",
                "tags": ["management"] if name == "observer" else [],
            }
            for name, password in users.items()
        ],
        "vhosts": [{"name": VHOST}],
        "permissions": [
            {
                "user": name,
                "vhost": VHOST,
                "configure": r"^$",
                "write": r"^markos\.personal\.events$"
                if name in {"chatpub", "chatworker"}
                else r"^$",
                "read": read[name],
            }
            for name in users
        ],
        "topic_permissions": [
            {
                "user": name,
                "vhost": VHOST,
                "exchange": "markos.personal.events",
                "write": write[name],
                "read": ".*",
            }
            for name in users
            if name != "observer"
        ],
        "parameters": [
            {
                "vhost": VHOST,
                "component": "vhost-limits",
                "name": "limits",
                "value": {"max-queues": 64, "max-connections": 32},
            }
        ],
        "exchanges": topology["exchanges"],
        "queues": topology["queues"],
        "bindings": topology["bindings"],
    }


def database(login: str) -> str:
    return make_conninfo(
        host=DATABASE_HOST,
        port=5432,
        dbname="markos",
        user=login,
        password=PASSWORD,
        sslmode="verify-full",
        sslrootcert="/etc/markos/tls/supabase-ca.pem",
    )


def config(component: str, service: str) -> ProductionServiceConfig:
    return ProductionServiceConfig.model_validate(
        {
            "schema_version": 1,
            "profile": "control-process-production-v1",
            "component": component,
            "service_id": service,
            "owner_id": UUID(OWNER),
            "domain": "personal",
            "api_origin": "https://" + DOMAIN,
            "issuer_endpoint": "https://issuer:19080/fixture",
            "health_port": 8780,
            "ca_file": "/etc/markos/tls/ca.pem",
            "certificate_file": "/etc/markos/tls/server.pem",
            "private_key_file": "/run/secrets/tls-server.key",
            "database_host": DATABASE_HOST,
            "database_ca_file": "/etc/markos/tls/supabase-ca.pem",
            "broker_publisher_user": "chatworker" if component == "activity" else "chatpub",
            "spaces": {
                "endpoint": "https://spaces.markos-ci.test",
                "region": "ci",
                "bucket": "markos-ci-bills",
            }
            if component == "gateway"
            else None,
            "google_client_id": "123456789-markosci.apps.googleusercontent.com"
            if component == "issuer"
            else None,
            "google_signin_subjects": {"100000000000000000001": "owner"}
            if component == "issuer"
            else None,
        }
    )


def write(path: Path, value: str | bytes, mode: int) -> None:
    path.write_bytes(value if isinstance(value, bytes) else value.encode() + b"\n")
    path.chmod(mode)


def prepare() -> None:
    project()
    if FOLDER.exists():
        raise RuntimeError("existing CI folder preserved; choose a fresh checkout")
    FOLDER.mkdir(mode=0o700)
    tls, standin, secrets, configs = (ETC / d for d in ("tls", "standin", "secrets", "config"))
    for directory in (ETC, tls, standin, configs):
        directory.mkdir(parents=True, mode=0o755)
    secrets.mkdir(mode=0o700)
    authority(FOLDER, "ca")
    certificate(FOLDER, "server", "ca", ["gateway", "issuer", "rabbitmq"])
    authority(FOLDER, "supabase-ca")
    certificate(FOLDER, "db", "supabase-ca", [DATABASE_HOST])
    for name in ("ca", "server", "supabase-ca"):
        write(tls / (name + ".pem"), (FOLDER / (name + ".pem")).read_bytes(), 0o644)
    for name in ("db.pem", "db.key"):
        write(standin / name, (FOLDER / name).read_bytes(), 0o644)
    users = {
        name: tokens.token_urlsafe(32)
        for name in ("chatpub", "chatworker", "chatactivity", "observer")
    }
    broker = {
        role: f"amqps://{user}:{users[user]}@rabbitmq:5671/{VHOST}"
        for role, user in {
            "publisher": "chatpub",
            "worker": "chatworker",
            "activity": "chatactivity",
        }.items()
    }
    values = {
        "gateway-database-dsn": database("m7_gateway"),
        "issuer-database-dsn": database("m7_issuer"),
        "publisher-database-dsn": database("m7_pub"),
        "worker-database-dsn": database("m7_worker"),
        "activity-database-dsn": database("m7_activity"),
        "mail-database-dsn": database("m7_mail"),
        "broker-publisher-dsn": broker["publisher"],
        "broker-worker-dsn": broker["worker"],
        "broker-activity-dsn": broker["activity"],
        "audit-user": "observer",
        "audit-password": users["observer"],
        "issuer-api-token": tokens.token_urlsafe(32),
        "issuer-worker-token": tokens.token_urlsafe(32),
        "issuer-admin-token": tokens.token_urlsafe(32),
        "issuer-state-key": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "session-key": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "spaces-access-key": "synthetic-ci-access",
        "spaces-secret-key": "synthetic-ci-secret",
        "google-client-secret": "synthetic-ci-google",
        "mail-token-key": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    }
    for name, value in values.items():
        write(secrets / name, value, 0o400)
    # The TLS key is multi-line PEM, so the private key file is not a FileSecrets value.
    write(secrets / "tls-server-key", (FOLDER / "server.key").read_bytes(), 0o400)
    write(secrets / "rabbitmq-tls-key", (FOLDER / "server.key").read_bytes(), 0o400)
    write(secrets / "rabbitmq-definitions", json.dumps(definitions(users)), 0o400)
    for component, (service, _) in SERVICES.items():
        write(configs / (component + ".json"), config(component, service).to_wire(), 0o644)
    if sys.platform == "linux":
        # Production layout: each secret is owned by the one container user that reads it.
        rabbit = [str(secrets / n) for n in ("rabbitmq-tls-key", "rabbitmq-definitions")]
        markos = [str(p) for p in secrets.iterdir() if str(p) not in rabbit]
        command(["sudo", "-n", "chown", "10001:10001", *markos])
        command(["sudo", "-n", "chown", "999:999", *rabbit])


def admin(dbname: str = "postgres") -> str:
    return make_conninfo(
        host="127.0.0.1",
        port=port("MARKOS_CI_POSTGRES_PORT"),
        dbname=dbname,
        user="postgres",
        password=PASSWORD,
        sslmode="require",
    )


async def bootstrap() -> None:
    project()
    logins = sorted({login for _, login in SERVICES.values()} | {"m7_mail"})
    groups = ("markos_migrator", "markos_runtime_client", "markos_control_gateway")
    with psycopg.connect(admin(), autocommit=True) as conn:
        for role in (*groups, "markos_issuer_client", *logins):
            can_login = role == "markos_migrator" or role in logins
            conn.execute(
                sql.SQL(
                    "CREATE ROLE {} {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD {}"
                ).format(
                    sql.Identifier(role),
                    sql.SQL("LOGIN" if can_login else "NOLOGIN"),
                    sql.Literal(PASSWORD),
                )
            )
        conn.execute("GRANT markos_control_gateway TO m7_gateway")
        conn.execute("GRANT markos_issuer_client TO m7_issuer")
        conn.execute("GRANT markos_runtime_client TO m7_pub,m7_worker,m7_activity,m7_mail")
        conn.execute("CREATE DATABASE markos OWNER markos_migrator")
    migration = make_conninfo(admin("markos"), user="markos_migrator")
    async with await psycopg.AsyncConnection.connect(
        migration, autocommit=True, row_factory=dict_row
    ) as conn:
        await migrate(conn, ROOT / "infrastructure/migrations")
        await conn.execute("INSERT INTO markos_control.gateway_logins VALUES('m7_gateway')")
        for login, service, pub, consume, execute in [
            ("m7_pub", "control-gateway", True, False, False),
            ("m7_worker", "control-test-agent", True, True, True),
            ("m7_activity", "control-activity", False, True, False),
        ]:
            await conn.execute(
                "INSERT INTO markos_runtime.service_identities(login_role,service_id,owner_id,"
                "domain,can_publish,can_consume,can_execute) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (login, service, UUID(OWNER), "personal", pub, consume, execute),
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Owned CI production boot only; no deployment")
    parser.add_argument("action", choices=["prepare", "build", "bootstrap", "start", "stop"])
    parser.add_argument("--confirm-synthetic", action="store_true", required=True)
    args = parser.parse_args()
    project()
    if args.action == "prepare":
        prepare()
    elif args.action == "build":
        compose("build", "gateway", timeout=900)
    elif args.action == "bootstrap":
        compose("up", "-d", "--wait", "--wait-timeout", "120", "supabase")
        asyncio.run(bootstrap())
    elif args.action == "start":
        compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "240")
        print("Production profile booted against CI stand-ins.")
    else:
        compose("down", "-v", "--remove-orphans")


if __name__ == "__main__":
    main()

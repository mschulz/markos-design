"""Owner-run setup for the production profile: TLS, secrets, configs, broker and database.

It runs inside the runtime image as root, through `infrastructure/provision.sh`, against
the host folder `/etc/markos`. It never prints a secret. Each step skips files that
already exist, so a rerun is safe. It never rotates a secret.
"""

import argparse
import asyncio
import base64
import getpass
import hashlib
import json
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from markos_runtime.contracts.base import ContractError, Domain
from markos_runtime.contracts.control import ControlValue
from markos_runtime.control_lifecycle.config import GoogleSubject, MarkosSubject
from markos_runtime.control_lifecycle.production import (
    ProductionServiceConfig,
    SpacesSettings,
    _dns_name,
    production_dsn,
)

ETC = Path("/etc/markos")
VHOST = "markos"
ISSUER_ENDPOINT = "https://issuer:19080/issuer"
# Uids of the container users that read each secret.
MARKOS_UID, RABBITMQ_UID = 10001, 999
RABBITMQ_SECRETS = {"rabbitmq-definitions", "rabbitmq-tls-key"}
# Component: (service identity, database secret). Mail stays off; its login is made anyway.
SERVICES = {
    "gateway": ("control-gateway", "gateway"),
    "issuer": ("control-gateway", "issuer"),
    "publisher": ("control-gateway", "publisher"),
    "completion-publisher": ("control-test-agent", "worker"),
    "consumer": ("control-test-agent", "worker"),
    "activity": ("control-activity", "activity"),
    "worker": ("control-test-agent", "worker"),
}
# Database secret: login role.
LOGINS = {
    "migrator": "markos_migrator",
    "gateway": "markos_gateway",
    "issuer": "markos_issuer",
    "publisher": "markos_pub",
    "worker": "markos_worker",
    "activity": "markos_activity",
    "mail": "markos_mail",
}
GROUPS = ("markos_runtime_client", "markos_control_gateway", "markos_issuer_client")
GRANTS = {
    "markos_control_gateway": ("markos_gateway",),
    "markos_issuer_client": ("markos_issuer",),
    "markos_runtime_client": ("markos_pub", "markos_worker", "markos_activity", "markos_mail"),
}
# login, service identity, publish, consume, execute
IDENTITIES = [
    ("markos_pub", "control-gateway", True, False, False),
    ("markos_worker", "control-test-agent", True, True, True),
    ("markos_activity", "control-activity", False, True, False),
]
# Broker user: (DSN secret or None, read queue, topic write)
BROKER = {
    "chatpub": ("broker-publisher-dsn", r"^$", r"^v1\.control\.chat-requested$"),
    "chatworker": (
        "broker-worker-dsn",
        r"^markos\.personal\.control-test-agent$",
        r"^v1\.control\.chat-completed$",
    ),
    "chatactivity": ("broker-activity-dsn", r"^markos\.personal\.control-activity$", r"^$"),
}
# Secrets the owner supplies with `put-secret`. Everything else is generated.
OWNER_SECRETS = {
    "supabase-admin-dsn",
    "google-client-secret",
    "spaces-access-key",
    "spaces-secret-key",
}
TOPOLOGY = {
    "exchanges": [
        {
            "name": "markos.personal.events",
            "vhost": VHOST,
            "type": "topic",
            "durable": True,
            "auto_delete": False,
            "internal": False,
            "arguments": {},
        }
    ],
    "queues": [
        {
            "name": "markos.personal." + name,
            "vhost": VHOST,
            "durable": True,
            "auto_delete": False,
            "arguments": {
                "x-queue-type": "classic",
                "x-max-length": 10000,
                "x-max-length-bytes": 33554432,
                "x-overflow": "reject-publish",
            },
        }
        for name in ("control-test-agent", "control-activity")
    ],
    "bindings": [
        {
            "source": "markos.personal.events",
            "vhost": VHOST,
            "destination": "markos.personal." + queue,
            "destination_type": "queue",
            "routing_key": key,
            "arguments": {},
        }
        for queue, key in (
            ("control-test-agent", "v1.control.chat-requested"),
            ("control-activity", "v1.control.chat-completed"),
        )
    ],
}


class Site(ControlValue):
    """The owner's choices, in `/etc/markos/site.json`. Nothing here is secret."""

    domain: str
    owner_id: UUID
    owner_domain: Domain = "personal"
    database_host: str
    database_name: str = Field(default="postgres", pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    google_client_id: str
    google_subject: GoogleSubject
    markos_subject: MarkosSubject
    spaces: SpacesSettings

    @field_validator("domain", "database_host")
    @classmethod
    def dns(cls, value: str) -> str:
        return _dns_name(value)


def load_site(etc: Path) -> Site:
    try:
        return Site.model_validate_json((etc / "site.json").read_bytes())
    except (OSError, ValueError):
        raise ContractError("valid site.json required; see docs/m7-runbook.md") from None


def write(path: Path, value: bytes, mode: int, uid: int = MARKOS_UID) -> bool:
    """Create a file once. Return False, and change nothing, if it exists."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    except FileExistsError:
        return False
    with os.fdopen(fd, "wb") as handle:
        handle.write(value)
    os.chmod(path, mode)
    if os.geteuid() == 0:
        os.chown(path, uid, uid)
    return True


def put_secret(etc: Path, name: str, value: str) -> bool:
    uid = RABBITMQ_UID if name in RABBITMQ_SECRETS else MARKOS_UID
    return write(etc / "secrets" / name, value.encode(), 0o400, uid)


def read_secret(etc: Path, name: str) -> str:
    try:
        return (etc / "secrets" / name).read_text().strip()
    except OSError:
        raise ContractError("missing secret: " + name) from None


def folders(etc: Path) -> None:
    for name, mode in (("", 0o755), ("tls", 0o755), ("config", 0o755), ("secrets", 0o700)):
        (etc / name).mkdir(mode=mode, exist_ok=True)
        os.chmod(etc / name, mode)


def tls(etc: Path) -> None:
    """A private CA and one server certificate for gateway, issuer and rabbitmq."""
    from cryptography import x509  # type: ignore[import-not-found,unused-ignore]
    from cryptography.hazmat.primitives import (  # type: ignore[import-not-found,unused-ignore]
        hashes,
        serialization,
    )
    from cryptography.hazmat.primitives.asymmetric import (  # type: ignore[import-not-found,unused-ignore]
        ec,
    )
    from cryptography.x509 import oid  # type: ignore[import-not-found,unused-ignore]

    ExtendedKeyUsageOID, NameOID = oid.ExtendedKeyUsageOID, oid.NameOID

    if (etc / "tls" / "ca.pem").exists():
        return
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MarkOS private CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "gateway")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, False, False, False, False),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(n) for n in ("gateway", "issuer", "rabbitmq")]
            ),
            False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False
        )
        .sign(ca_key, hashes.SHA256())
    )
    pem = serialization.Encoding.PEM
    private = key.private_bytes(
        pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    # The CA key is not kept: a new server certificate means a new CA.
    write(etc / "secrets" / "tls-server-key", private, 0o400, MARKOS_UID)
    write(etc / "secrets" / "rabbitmq-tls-key", private, 0o400, RABBITMQ_UID)
    write(etc / "tls" / "server.pem", server.public_bytes(pem), 0o644, 0)
    write(etc / "tls" / "ca.pem", ca.public_bytes(pem), 0o644, 0)


def token() -> str:
    return secrets.token_urlsafe(32)


def key32() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def database_dsn(site: Site, login: str, password: str) -> str:
    return (
        f"host={site.database_host} port=5432 dbname={site.database_name} user={login} "
        f"password={password} sslmode=verify-full sslrootcert=/etc/markos/tls/supabase-ca.pem"
    )


def generate(etc: Path, site: Site) -> list[str]:
    """Create every generated secret that does not exist yet. Return the names created."""
    values = {
        **{
            name + "-database-dsn": database_dsn(site, login, token())
            for name, login in LOGINS.items()
        },
        **{
            dsn: f"amqps://{user}:{quote(token(), safe='')}@rabbitmq:5671/{VHOST}"
            for user, (dsn, _, _) in BROKER.items()
        },
        "audit-user": "observer",
        "audit-password": token(),
        "issuer-api-token": token(),
        "issuer-worker-token": token(),
        "issuer-admin-token": token(),
        "issuer-state-key": key32(),
        "session-key": key32(),
        "mail-token-key": key32(),
    }
    return [name for name, value in values.items() if put_secret(etc, name, value)]


def rabbit_hash(password: str) -> str:
    salt = os.urandom(4)
    return base64.b64encode(salt + hashlib.sha256(salt + password.encode()).digest()).decode()


def definitions(etc: Path) -> dict[str, Any]:
    """Users, permissions and the reviewed control topology, loaded at broker boot."""
    passwords = {"observer": read_secret(etc, "audit-password")}
    for user, (dsn, _, _) in BROKER.items():
        url = urlsplit(read_secret(etc, dsn))
        if url.username != user or not url.password:
            raise ContractError("broker DSN does not match its user: " + dsn)
        passwords[user] = url.password
    return {
        "users": [
            {
                "name": user,
                "password_hash": rabbit_hash(password),
                "hashing_algorithm": "rabbit_password_hashing_sha256",
                "tags": ["management"] if user == "observer" else [],
            }
            for user, password in passwords.items()
        ],
        "vhosts": [{"name": VHOST}],
        "permissions": [
            {
                "user": user,
                "vhost": VHOST,
                "configure": r"^$",
                "write": r"^markos\.personal\.events$"
                if user in {"chatpub", "chatworker"}
                else r"^$",
                "read": BROKER[user][1] if user in BROKER else r"^$",
            }
            for user in passwords
        ],
        "topic_permissions": [
            {
                "user": user,
                "vhost": VHOST,
                "exchange": "markos.personal.events",
                "write": write_key,
                "read": ".*",
            }
            for user, (_, _, write_key) in BROKER.items()
        ],
        "parameters": [
            {
                "vhost": VHOST,
                "component": "vhost-limits",
                "name": "limits",
                "value": {"max-queues": 64, "max-connections": 32},
            }
        ],
        **TOPOLOGY,
    }


def config(site: Site, component: str) -> ProductionServiceConfig:
    service, _ = SERVICES[component]
    return ProductionServiceConfig.model_validate(
        {
            "schema_version": 1,
            "profile": "control-process-production-v1",
            "component": component,
            "service_id": service,
            "owner_id": site.owner_id,
            "domain": site.owner_domain,
            "api_origin": "https://" + site.domain,
            "issuer_endpoint": ISSUER_ENDPOINT,
            "health_port": 8780,
            "ca_file": "/etc/markos/tls/ca.pem",
            "certificate_file": "/etc/markos/tls/server.pem",
            "private_key_file": "/run/secrets/tls-server.key",
            "database_host": site.database_host,
            "database_ca_file": "/etc/markos/tls/supabase-ca.pem",
            "broker_publisher_user": "chatworker" if component == "activity" else "chatpub",
            "spaces": site.spaces if component == "gateway" else None,
            "google_client_id": site.google_client_id if component == "issuer" else None,
            "google_signin_subjects": {site.google_subject: site.markos_subject}
            if component == "issuer"
            else None,
        }
    )


def configs(etc: Path, site: Site) -> list[str]:
    built = {name: config(site, name).to_wire() for name in SERVICES}
    return [
        name
        for name, body in built.items()
        if write(etc / "config" / (name + ".json"), body, 0o644, 0)
    ]


def password(dsn: str) -> str:
    from psycopg.conninfo import conninfo_to_dict

    value = conninfo_to_dict(dsn).get("password")
    if not isinstance(value, str) or not value:
        raise ContractError("database DSN without a password")
    return value


async def database(
    etc: Path, site: Site, migrations: Path, admin_dsn: str, reach: dict[str, Any] | None = None
) -> None:
    """Groups, logins, migrations, service identities and the owner's principal.

    Safe to rerun: roles that exist get their password reset to the stored secret, and
    rows that exist are kept. `reach` overrides where the migrator connects (CI only).
    """
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row

    from markos_runtime.store.migrations import migrate

    logins = {login: password(read_secret(etc, n + "-database-dsn")) for n, login in LOGINS.items()}
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as conn:
        for role in (*GROUPS, *logins):
            exists = await (
                await conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
            ).fetchone()
            verb = "ALTER" if exists else "CREATE"
            clause = (
                sql.SQL("LOGIN PASSWORD {}").format(sql.Literal(logins[role]))
                if role in logins
                else sql.SQL("NOLOGIN")
            )
            await conn.execute(
                sql.SQL("{} ROLE {} {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS").format(
                    sql.SQL(verb), sql.Identifier(role), clause
                )
            )
        for group, members in GRANTS.items():
            await conn.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(group), sql.SQL(",").join(map(sql.Identifier, members))
                )
            )
        await conn.execute(
            sql.SQL("GRANT CREATE ON DATABASE {} TO markos_migrator").format(
                sql.Identifier(site.database_name)
            )
        )
    migrator = make_conninfo(
        read_secret(etc, "migrator-database-dsn"),
        sslrootcert=str(etc / "tls" / "supabase-ca.pem"),
        **(reach or {}),
    )
    async with await psycopg.AsyncConnection.connect(
        migrator, autocommit=True, row_factory=dict_row
    ) as conn:
        await migrate(conn, migrations)
        await conn.execute(
            "INSERT INTO markos_control.gateway_logins SELECT 'markos_gateway' WHERE NOT EXISTS"
            " (SELECT 1 FROM markos_control.gateway_logins WHERE login_role='markos_gateway')"
        )
        for login, service, pub, consume, execute in IDENTITIES:
            await conn.execute(
                "INSERT INTO markos_runtime.service_identities(login_role,service_id,owner_id,"
                "domain,can_publish,can_consume,can_execute) SELECT %s,%s,%s,%s,%s,%s,%s "
                "WHERE NOT EXISTS (SELECT 1 FROM markos_runtime.service_identities "
                "WHERE login_role=%s)",
                (login, service, site.owner_id, site.owner_domain, pub, consume, execute, login),
            )
        principal = await (
            await conn.execute(
                "SELECT principal_id FROM markos_control.principals "
                "WHERE issuer_id='synthetic-issuer' AND subject=%s",
                (site.markos_subject,),
            )
        ).fetchone()
        if principal is None:
            ident = uuid4()
            await conn.execute(
                "INSERT INTO markos_control.principals VALUES(%s,'synthetic-issuer',%s,false)",
                (ident, site.markos_subject),
            )
            await conn.execute(
                "INSERT INTO markos_control.memberships VALUES(%s,%s,%s,1,%s)",
                (ident, site.owner_id, site.owner_domain, ["administrator", "operator"]),
            )


async def reapply_tombstones(etc: Path, site: Site) -> int:
    """After a database restore, replay the tombstone log from Spaces. Returns entries applied."""
    import psycopg
    from psycopg.conninfo import make_conninfo
    from psycopg.types.json import Jsonb

    from markos_runtime.knowledge.spaces import SpacesObjects

    spaces = SpacesObjects(
        site.spaces.endpoint,
        site.spaces.region,
        site.spaces.bucket,
        read_secret(etc, "spaces-access-key"),
        read_secret(etc, "spaces-secret-key"),
    )
    entries = [json.loads(body) for body in spaces.tombstones()]
    for entry in entries:
        if entry["kind"] == "bill":
            # The backup may predate the erasure, so erase again. A missing original is fine.
            spaces.delete(
                UUID(entry["owner_id"]), entry["domain"], entry["source_sha256"], entry["suffix"]
            )
    spaces.http.close()
    migrator = make_conninfo(
        read_secret(etc, "migrator-database-dsn"),
        sslrootcert=str(etc / "tls" / "supabase-ca.pem"),
    )
    async with await psycopg.AsyncConnection.connect(migrator, autocommit=True) as conn:
        row = await (
            await conn.execute("SELECT markos_control.reapply_tombstones(%s)", (Jsonb(entries),))
        ).fetchone()
    assert row is not None
    return int(row[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="provision", description="Owner-run production setup. Never prints a secret."
    )
    parser.add_argument("--etc", type=Path, default=ETC)
    steps = parser.add_subparsers(dest="step", required=True)
    steps.add_parser("tls", help="Private CA and server certificate")
    steps.add_parser("secrets", help="Generate every secret that does not exist yet")
    put = steps.add_parser("put-secret", help="Store one owner-supplied secret from stdin")
    put.add_argument("name", choices=sorted(OWNER_SECRETS))
    steps.add_parser("configs", help="Write the service configs from site.json")
    steps.add_parser("definitions", help="Write the broker definitions secret")
    db = steps.add_parser("database", help="Roles, migrations and identities on Supabase")
    db.add_argument("--migrations", type=Path, default=Path("/opt/migrations"))
    steps.add_parser("reapply-tombstones", help="After a restore, replay deletions from Spaces")
    args = parser.parse_args(argv)
    etc: Path = args.etc
    try:
        folders(etc)
        if args.step == "tls":
            tls(etc)
            print("TLS files ready.")
        elif args.step == "put-secret":
            prompt = args.name + ": "
            value = getpass.getpass(prompt) if sys.stdin.isatty() else sys.stdin.readline()
            value = value.strip()
            if not value or "\n" in value:
                print("Empty value; nothing stored.", file=sys.stderr)
                return 2
            if not put_secret(etc, args.name, value):
                print(args.name + " exists; nothing changed.", file=sys.stderr)
                return 1
            print("Stored " + args.name + ".")
        elif args.step == "secrets":
            made = generate(etc, load_site(etc))
            print("Created: " + (", ".join(made) or "nothing (all exist)"))
        elif args.step == "configs":
            made = configs(etc, load_site(etc))
            print("Created: " + (", ".join(made) or "nothing (all exist)"))
        elif args.step == "definitions":
            body = json.dumps(definitions(etc), indent=1).encode()
            if not put_secret(etc, "rabbitmq-definitions", body.decode()):
                print("rabbitmq-definitions exists; nothing changed.", file=sys.stderr)
                return 1
            print("Stored rabbitmq-definitions.")
        elif args.step == "reapply-tombstones":
            applied = asyncio.run(reapply_tombstones(etc, load_site(etc)))
            print(f"Reapplied {applied} tombstones.")
        else:
            site = load_site(etc)
            admin = read_secret(etc, "supabase-admin-dsn")
            try:
                production_dsn(admin, site.database_host, str(etc / "tls" / "supabase-ca.pem"))
            except Exception:
                print("supabase-admin-dsn must use verify-full; see the runbook.", file=sys.stderr)
                return 2
            asyncio.run(database(etc, site, args.migrations, admin))
            print("Database ready.")
    except ContractError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

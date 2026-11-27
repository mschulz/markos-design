"""CI-only controller: boots compose.production.yml against disposable stand-ins.

Never shipped in the runtime image. It sets up infrastructure/.m7-production with the
owner's provisioning code (`markos_runtime.provision`) and drives only its own project.
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from psycopg.conninfo import make_conninfo

from markos_runtime import provision
from markos_runtime.control_lifecycle.production import SpacesSettings

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "infrastructure/.m7-production"
ETC = FOLDER / "etc"
DOMAIN = "markos.markos-ci.test"
DATABASE_HOST = "db.markos-ci.test"
PASSWORD = "synthetic-test-only"
SERVICES = provision.SERVICES
CERT_DAYS = "2"
SITE = provision.Site(
    schema_version=1,
    domain=DOMAIN,
    owner_id=UUID("11111111-1111-4111-8111-111111111111"),
    database_host=DATABASE_HOST,
    google_client_id="123456789-markosci.apps.googleusercontent.com",
    google_subject="100000000000000000001",
    markos_subject="owner",
    spaces=SpacesSettings(
        endpoint="https://spaces.markos-ci.test", region="ci", bucket="markos-ci-bills"
    ),
)


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
    os.environ.update(
        MARKOS_IMAGE_TAG=project(),
        MARKOS_DOMAIN=DOMAIN,
        MARKOS_ETC=str(ETC),
        MARKOS_CI_POSTGRES_PORT=str(port("MARKOS_CI_POSTGRES_PORT")),
        MARKOS_CI_HTTPS_PORT=str(port("MARKOS_CI_HTTPS_PORT")),
    )
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


def authority(name: str) -> None:
    command(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", CERT_DAYS]
        + ["-subj", "/CN=" + name, "-addext", "basicConstraints=critical,CA:TRUE"]
        + ["-addext", "keyUsage=critical,keyCertSign,cRLSign"]
        + ["-keyout", str(FOLDER / (name + ".key")), "-out", str(FOLDER / (name + ".pem"))]
    )


def certificate(name: str, ca: str, names: list[str]) -> None:
    """A leaf certificate signed by `ca`, with the extensions strict X.509 checks need."""
    key, csr, ext = (FOLDER / (name + s) for s in (".key", ".csr", ".ext"))
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
        + ["-CA", str(FOLDER / (ca + ".pem")), "-CAkey", str(FOLDER / (ca + ".key"))]
        + ["-CAcreateserial", "-extfile", str(ext), "-out", str(FOLDER / (name + ".pem"))]
    )


def prepare() -> None:
    """The owner's steps. Only the Supabase stand-in's certificate comes from openssl."""
    project()
    if FOLDER.exists():
        raise RuntimeError("existing CI folder preserved; choose a fresh checkout")
    FOLDER.mkdir(mode=0o700)
    ETC.mkdir(mode=0o755)
    provision.folders(ETC)
    provision.tls(ETC)
    authority("supabase-ca")
    certificate("db", "supabase-ca", [DATABASE_HOST])
    (ETC / "standin").mkdir(mode=0o755)
    for source, target in [
        ("supabase-ca.pem", "tls/supabase-ca.pem"),
        ("db.pem", "standin/db.pem"),
        ("db.key", "standin/db.key"),
    ]:
        provision.write(ETC / target, (FOLDER / source).read_bytes(), 0o644)
    (ETC / "site.json").write_bytes(SITE.to_wire())
    provision.generate(ETC, provision.load_site(ETC))
    for name in sorted(provision.OWNER_SECRETS - {"supabase-admin-dsn"}):
        provision.put_secret(ETC, name, "synthetic-ci-" + name)
    provision.put_secret(ETC, "rabbitmq-definitions", json.dumps(provision.definitions(ETC)))
    provision.configs(ETC, provision.load_site(ETC))


def admin() -> str:
    return make_conninfo(
        host="127.0.0.1",
        port=port("MARKOS_CI_POSTGRES_PORT"),
        dbname="postgres",
        user="postgres",
        password=PASSWORD,
        sslmode="require",
    )


def bootstrap() -> None:
    project()
    reach = {"hostaddr": "127.0.0.1", "port": port("MARKOS_CI_POSTGRES_PORT")}
    migrations = ROOT / "infrastructure/migrations"
    asyncio.run(provision.database(ETC, SITE, migrations, admin(), reach))
    # A rerun must change nothing and must not fail.
    asyncio.run(provision.database(ETC, SITE, migrations, admin(), reach))
    if sys.platform == "linux":
        # Production layout: each secret is owned by the one container user that reads it.
        secrets = ETC / "secrets"
        rabbit = [str(secrets / n) for n in sorted(provision.RABBITMQ_SECRETS)]
        markos = [str(p) for p in secrets.iterdir() if str(p) not in rabbit]
        command(["sudo", "-n", "chown", "10001:10001", *markos])
        command(["sudo", "-n", "chown", "999:999", *rabbit])


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
        bootstrap()
    elif args.action == "start":
        compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "240")
        print("Production profile booted against CI stand-ins.")
    else:
        compose("down", "-v", "--remove-orphans")


if __name__ == "__main__":
    main()

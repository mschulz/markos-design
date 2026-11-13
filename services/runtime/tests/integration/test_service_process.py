import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import psycopg


def test_synthetic_service_process_health_signal_shutdown_and_no_startup_migration(
    database: dict[str, str], tmp_path: Path
) -> None:
    directory = Path(__file__).resolve().parents[4] / "config/runtime/lifecycle"
    doc = json.loads((directory / "worker.json").read_text())
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    doc["health_port"] = port
    (tmp_path / "worker.json").write_text(json.dumps(doc))
    (tmp_path / "subscriptions.json").write_bytes((directory / "subscriptions.json").read_bytes())
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-m",
            "markos_runtime",
            "serve-synthetic",
            "--confirm-synthetic",
            "--config",
            str(tmp_path / "worker.json"),
        ],
        env={**os.environ, "MARKOS_RUNTIME_DATABASE_DSN": database["consumer"]},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        end = time.monotonic() + 10
        while time.monotonic() < end:
            try:
                with urlopen(f"http://127.0.0.1:{port}/ready", timeout=1) as response:
                    if response.status == 200:
                        break
            except (URLError, OSError):
                time.sleep(0.05)
        else:
            raise AssertionError("service did not become ready")
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=15)
        assert process.returncode == 0 and "synthetic-test-only" not in out + err
        assert "stopped" in out
        with psycopg.connect(database["consumer"]) as conn:
            assert conn.execute(
                "SELECT status FROM markos_runtime.service_heartbeats WHERE component='worker'"
            ).fetchone() == ("draining",)
        with psycopg.connect(database["admin"]) as conn:
            assert conn.execute(
                "SELECT count(*) FROM markos_runtime.schema_migrations"
            ).fetchone() == (12,)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

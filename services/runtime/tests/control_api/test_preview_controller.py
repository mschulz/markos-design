"""Alternate local preview ports must not redirect provisioning or container SQL."""

from typing import Any

import pytest
from psycopg.conninfo import conninfo_to_dict

from tests.control_process.support import admin, container_database, postgres_port


@pytest.mark.parametrize("port", ["5432", "15432", "1024", "65535"])
def test_preview_host_port_keeps_container_listener_fixed(monkeypatch: Any, port: str) -> None:
    monkeypatch.setenv("MARKOS_PREVIEW_POSTGRES_PORT", port)
    assert postgres_port() == int(port)
    assert conninfo_to_dict(admin())["host"] == "127.0.0.1"
    assert conninfo_to_dict(admin())["port"] == port
    internal = conninfo_to_dict(container_database("m1g_gateway"))
    assert internal["host"] == "postgres" and internal["port"] == "5432"
    assert internal["user"] == "m1g_gateway"


@pytest.mark.parametrize(
    "port",
    ["", "5432 host=elsewhere", "5432\n", "localhost:5432", "0", "1023", "65536", "015432", "-1"],
)
def test_invalid_preview_port_cannot_become_a_database_dsn(monkeypatch: Any, port: str) -> None:
    monkeypatch.setenv("MARKOS_PREVIEW_POSTGRES_PORT", port)
    with pytest.raises(ValueError, match="bounded local preview"):
        admin()


def test_default_preview_port_is_compatible(monkeypatch: Any) -> None:
    monkeypatch.delenv("MARKOS_PREVIEW_POSTGRES_PORT", raising=False)
    assert postgres_port() == 5432

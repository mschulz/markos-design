"""Explicit reuse of disposable PostgreSQL provisioning."""

# The base runtime intentionally omits HTTP/JWT libraries. Required API CI installs the extra.
import importlib.util
import os

import pytest

from tests.integration.conftest import admin_dsn, database
from tests.integration.test_control_persistence import control

if importlib.util.find_spec("jwt") is None:
    if os.environ.get("MARKOS_REQUIRE_API_TESTS") == "1":
        pytest.fail("required API profile dependencies missing")
    pytest.skip("optional control profile not installed", allow_module_level=True)

__all__ = ["admin_dsn", "database", "control"]

"""Reuse only explicit isolated fixtures; missing required browser support fails CI."""

import importlib.util
import os

import pytest

from tests.integration.conftest import admin_dsn, database
from tests.integration.test_control_persistence import control

if importlib.util.find_spec("playwright") is None or importlib.util.find_spec("jwt") is None:
    if os.environ.get("MARKOS_REQUIRE_BROWSER_TESTS") == "1":
        pytest.fail("required pinned browser dependencies missing")
    pytest.skip("optional browser-test group not installed", allow_module_level=True)

__all__ = ["admin_dsn", "database", "control", "conversation"]

if importlib.util.find_spec("jwt") is not None:
    from tests.control_api.test_conversation import conversation

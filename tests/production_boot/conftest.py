"""Production boot tests are opt-in and mandatory only in their dedicated CI job."""

import os

import pytest

if os.environ.get("MARKOS_REQUIRE_PRODUCTION_BOOT") != "1":
    pytest.skip("explicit owned production boot job only", allow_module_level=True)

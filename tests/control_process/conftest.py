"""Process tests are opt-in and mandatory only in their dedicated CI job."""

import os

import pytest

if os.environ.get("MARKOS_REQUIRE_CONTROL_PROCESS") != "1":
    pytest.skip("explicit owned synthetic control-process job only", allow_module_level=True)

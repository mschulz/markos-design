import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def event_document() -> dict[str, Any]:
    body = (ROOT / "config/runtime.event-v1.example.json").read_text()
    return cast(dict[str, Any], json.loads(body))


@pytest.fixture
def runtime_config(tmp_path: Path) -> Path:
    for name in ("runtime.example.toml", "runtime.subscriptions.json"):
        shutil.copyfile(ROOT / "config" / name, tmp_path / name)
    return tmp_path / "runtime.example.toml"


@pytest.fixture
def runtime_env() -> dict[str, str]:
    return {
        "MARKOS_RUNTIME_BROKER_DSN": "amqp://synthetic:synthetic@localhost:5672/test",
        "MARKOS_RUNTIME_DATABASE_DSN": "postgresql://synthetic:synthetic@localhost/test",
    }

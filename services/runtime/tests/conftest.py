import json
from pathlib import Path
from typing import Any

import pytest

OWNER = "11111111-1111-4111-8111-111111111111"
EVENT = "foundation.noop.requested"


@pytest.fixture
def event_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": "22222222-2222-4222-8222-222222222222",
        "event_type": EVENT,
        "owner_id": OWNER,
        "domain": "personal",
        "source": "foundation-generator",
        "occurred_at": "2026-11-12T00:00:00Z",
        "correlation_id": "33333333-3333-4333-8333-333333333333",
        "causation_id": None,
        "trace_id": "44444444-4444-4444-8444-444444444444",
        "sensitivity": "internal",
        "operation_key": "synthetic:noop:1",
        "payload": {"label": "Synthetic only"},
    }


@pytest.fixture
def manifest_document() -> dict[str, Any]:
    return {
        "policies": [
            {
                "service_id": "foundation-generator",
                "owner_id": OWNER,
                "domain": "personal",
                "publishes": [EVENT],
            },
            {
                "service_id": "foundation-worker",
                "owner_id": OWNER,
                "domain": "personal",
                "consumes": [EVENT],
            },
        ],
        "subscriptions": [
            {
                "service_id": "foundation-worker",
                "owner_id": OWNER,
                "domain": "personal",
                "event_type": EVENT,
            }
        ],
    }


@pytest.fixture
def config_file(tmp_path: Path, manifest_document: dict[str, Any]) -> Path:
    (tmp_path / "subscriptions.json").write_text(json.dumps(manifest_document))
    path = tmp_path / "runtime.toml"
    path.write_text(
        '[runtime]\nenvironment="test"\nrole="publisher"\n'
        'service_id="foundation-generator"\nmanifest_file="subscriptions.json"\n'
    )
    return path

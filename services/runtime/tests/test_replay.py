import json
from typing import Any

import pytest
from pydantic import ValidationError

from markos_runtime.contracts.replay import ReplayRequest


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"suppress_effects": False},
        {"suppress_effects": 1},
        {"suppress_effects": "true"},
        {"payload": {}},
        {"event_id": "00000000-0000-0000-0000-000000000000"},
        {"action": "send.email"},
        {"permit_id": "not-an-id"},
    ],
)
def test_replay_contract_cannot_enable_effects_or_replace_identity(change: dict[str, Any]) -> None:
    doc = {"schema_version": 1, "event_id": "11111111-1111-4111-8111-111111111111"}
    doc.update(change)
    with pytest.raises(ValidationError):
        ReplayRequest.model_validate_json(json.dumps(doc))

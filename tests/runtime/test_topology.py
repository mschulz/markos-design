import json
from pathlib import Path
from uuid import UUID

import pytest

from markos.runtime.contracts import ContractError, EventEnvelope
from markos.runtime.settings import ResourceLimits
from markos.runtime.topology import TopologyManifest

CONFIG = Path(__file__).resolve().parents[2] / "config"


def manifest() -> TopologyManifest:
    return TopologyManifest.from_bytes((CONFIG / "runtime.subscriptions.json").read_bytes())


def test_independent_subscribers_have_distinct_owner_scoped_queues() -> None:
    model = manifest()
    event = EventEnvelope.from_bytes((CONFIG / "runtime.event-v1.example.json").read_bytes())
    first, second = model.subscriptions
    assert first.queue_name != second.queue_name
    assert first.scope.owner_id.hex in first.queue_name
    first.validate_delivery_scope(event, first.scope)
    second.validate_delivery_scope(event, second.scope)
    model.authorize_publication(event, model.publishers[0].scope)
    with pytest.raises(ContractError, match="grant missing"):
        model.authorize_publication(event.model_copy(update={"source": first.scope.service_id}),
                                    first.scope)
    with pytest.raises(ContractError, match="scope"):
        first.validate_delivery_scope(event, second.scope)
    with pytest.raises(ContractError, match="scope"):
        first.validate_delivery_scope(event.model_copy(update={"source": "forged"}), first.scope)
    for changes in ({"owner_id": UUID(int=123)}, {"domain": "work"}):
        with pytest.raises(ContractError, match="scope"):
            first.validate_delivery_scope(event.model_copy(update=changes), first.scope)


@pytest.mark.parametrize("mutation", ["duplicate", "source", "owner", "version", "unknown",
                                     "duplicate-publisher", "duplicate-events"])
def test_invalid_or_escalated_manifest_fails(mutation: str) -> None:
    document = manifest().model_dump(mode="json")
    if mutation == "duplicate":
        document["subscriptions"].append(document["subscriptions"][0])
    elif mutation == "source":
        document["subscriptions"][0]["allowed_sources"] = ["unregistered"]
    elif mutation == "owner":
        document["subscriptions"][0]["scope"]["owner_id"] = "55555555-5555-4555-8555-555555555555"
    elif mutation == "version":
        document["schema_version"] = True
    elif mutation == "duplicate-publisher":
        document["publishers"].append(document["publishers"][0])
    elif mutation == "duplicate-events":
        document["publishers"][0]["event_types"] *= 2
    else:
        document["provision_all_domains"] = True
    with pytest.raises(ContractError):
        TopologyManifest.from_bytes(json.dumps(document).encode())


@pytest.mark.parametrize("limits", [
    {"max_queues": 1}, {"max_connections": 2}, {"max_channels_per_connection": 1},
])
def test_manifest_exceeding_effective_budget_fails(limits: dict[str, int]) -> None:
    model = manifest()
    if "max_channels_per_connection" in limits:
        document = model.model_dump(mode="json")
        document["publishers"][0]["channels_per_connection"] = 2
        model = TopologyManifest.from_bytes(json.dumps(document).encode())
    with pytest.raises(ContractError, match="budget"):
        model.validate_budget(ResourceLimits(**limits))


def test_duplicate_or_oversize_manifest_json_fails() -> None:
    for body in (b'{"environment":"test","environment":"production"}', b" " * 262145):
        with pytest.raises(ContractError):
            TopologyManifest.from_bytes(body)

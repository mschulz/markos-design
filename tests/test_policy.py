import json
from typing import Any
from uuid import UUID

import pytest

from markos_runtime.contracts import ContractError, EventEnvelope, Manifest


def load(document: dict[str, Any]) -> Manifest:
    return Manifest.from_wire(json.dumps(document).encode())


def test_authorized_handoff_and_routing(
    event_document: dict[str, Any], manifest_document: dict[str, Any]
) -> None:
    manifest = load(manifest_document)
    event = EventEnvelope.from_wire(json.dumps(event_document).encode())
    manifest.authorize_publish(event, "foundation-generator")
    task = manifest.task_request(
        event, "foundation-worker", UUID(int=1), trusted_publisher_id="foundation-generator"
    )
    assert (task.event_id, task.operation_key, task.owner_id) == (
        event.event_id,
        event.operation_key,
        event.owner_id,
    )
    subscription = manifest.subscriptions[0]
    assert subscription.queue == "markos.personal.foundation-worker"
    assert subscription.exchange == "markos.personal.events"
    assert subscription.routing_key == "v1.foundation.noop-requested"


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_id", "99999999-9999-4999-8999-999999999999"),
        ("domain", "work"),
        ("source", "foundation-worker"),
    ],
)
def test_forged_publisher_scope(
    event_document: dict[str, Any], manifest_document: dict[str, Any], field: str, value: str
) -> None:
    manifest = load(manifest_document)
    event_document[field] = value
    event = EventEnvelope.from_wire(json.dumps(event_document).encode())
    with pytest.raises(ContractError):
        manifest.authorize_publish(event, "foundation-generator")


@pytest.mark.parametrize("service", ["unknown", "foundation-generator"])
def test_consumer_must_be_granted(
    event_document: dict[str, Any], manifest_document: dict[str, Any], service: str
) -> None:
    event = EventEnvelope.from_wire(json.dumps(event_document).encode())
    with pytest.raises(ContractError):
        load(manifest_document).task_request(
            event, service, UUID(int=1), trusted_publisher_id="foundation-generator"
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate-policy",
        "duplicate-subscription",
        "ungranted",
        "wrong-domain",
        "wrong-owner",
        "missing-subscription",
        "unknown-event",
        "unknown-service",
        "extra-field",
    ],
)
def test_invalid_manifest(manifest_document: dict[str, Any], mutation: str) -> None:
    sub = manifest_document["subscriptions"][0]
    if mutation == "duplicate-policy":
        manifest_document["policies"].append(manifest_document["policies"][0])
    elif mutation == "duplicate-subscription":
        manifest_document["subscriptions"].append(sub.copy())
    elif mutation == "ungranted":
        sub["service_id"] = "foundation-generator"
    elif mutation == "wrong-domain":
        sub["domain"] = "work"
    elif mutation == "wrong-owner":
        sub["owner_id"] = "99999999-9999-4999-8999-999999999999"
    elif mutation == "missing-subscription":
        manifest_document["subscriptions"] = []
    elif mutation == "unknown-event":
        sub["event_type"] = "electricity.parsed"
    elif mutation == "unknown-service":
        sub["service_id"] = "unknown"
    else:
        sub["binding"] = "#"
    with pytest.raises(ContractError):
        load(manifest_document)


def test_consumer_checks_trusted_publisher_identity(
    event_document: dict[str, Any], manifest_document: dict[str, Any]
) -> None:
    event_document["source"] = "foundation-worker"
    event = EventEnvelope.from_wire(json.dumps(event_document).encode())
    with pytest.raises(ContractError):
        load(manifest_document).task_request(
            event, "foundation-worker", UUID(int=1), trusted_publisher_id="foundation-generator"
        )

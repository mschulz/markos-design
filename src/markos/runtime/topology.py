"""Reviewable publisher/subscriber manifest; no broker provisioning happens here."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from markos.runtime.contracts import (
    ContractError,
    ContractModel,
    Environment,
    EventEnvelope,
    EventType,
    Identifier,
    ServiceScope,
    validate_json_document,
    validate_publisher_scope,
)
from markos.runtime.settings import ResourceLimits

MAX_MANIFEST_BYTES = 262_144


class PublisherDefinition(ContractModel):
    scope: ServiceScope
    event_types: tuple[EventType, ...] = Field(min_length=1)
    connection_budget: int = Field(default=1, ge=1, le=32)
    channels_per_connection: int = Field(default=1, ge=1, le=32)

    @model_validator(mode="after")
    def unique_events(self) -> Self:
        if len(set(self.event_types)) != len(self.event_types):
            raise ValueError("duplicate publisher event type")
        return self


class Subscription(ContractModel):
    scope: ServiceScope
    event_types: tuple[EventType, ...] = Field(min_length=1)
    allowed_sources: tuple[Identifier, ...] = Field(min_length=1)
    connection_budget: int = Field(default=1, ge=1, le=32)
    channels_per_connection: int = Field(default=1, ge=1, le=32)

    @property
    def queue_name(self) -> str:
        return f"markos.{self.scope.domain}.{self.scope.owner_id.hex}.{self.scope.service_id}.v1"

    def validate_delivery_scope(self, event: EventEnvelope, trusted: ServiceScope) -> None:
        if (trusted != self.scope or event.owner_id != trusted.owner_id
                or event.domain != trusted.domain or event.event_type not in self.event_types
                or event.source not in self.allowed_sources):
            raise ContractError("consumer scope mismatch")


class TopologyManifest(ContractModel):
    schema_version: Literal[1]
    environment: Environment
    publishers: tuple[PublisherDefinition, ...] = Field(min_length=1, max_length=64)
    subscriptions: tuple[Subscription, ...] = Field(min_length=1, max_length=64)

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("manifest version must be an integer")
        return value

    @model_validator(mode="after")
    def identities_and_sources_match(self) -> Self:
        scopes = [p.scope for p in self.publishers]
        if len(set(scopes)) != len(scopes):
            raise ValueError("duplicate publisher scope")
        queues = [s.queue_name for s in self.subscriptions]
        if len(set(queues)) != len(queues):
            raise ValueError("duplicate subscription queue")
        for sub in self.subscriptions:
            if len(set(sub.event_types)) != len(sub.event_types):
                raise ValueError("duplicate subscription event type")
            if len(set(sub.allowed_sources)) != len(sub.allowed_sources):
                raise ValueError("duplicate source grant")
            for source in sub.allowed_sources:
                for event_type in sub.event_types:
                    if not any(p.scope.owner_id == sub.scope.owner_id
                               and p.scope.domain == sub.scope.domain
                               and p.scope.service_id == source and event_type in p.event_types
                               for p in self.publishers):
                        raise ValueError("unregistered publisher grant")
        return self

    def validate_budget(self, limits: ResourceLimits) -> None:
        definitions: tuple[PublisherDefinition | Subscription, ...] = (
            *self.publishers, *self.subscriptions
        )
        if (len(self.subscriptions) > limits.max_queues
                or sum(d.connection_budget for d in definitions) > limits.max_connections
                or any(d.channels_per_connection > limits.max_channels_per_connection
                       for d in definitions)):
            raise ContractError("topology exceeds resource budget")

    def authorize_publication(self, event: EventEnvelope, trusted: ServiceScope) -> None:
        validate_publisher_scope(event, trusted)
        if not any(p.scope == trusted and event.event_type in p.event_types
                   for p in self.publishers):
            raise ContractError("publisher grant missing")

    @classmethod
    def from_bytes(cls, body: bytes) -> Self:
        validate_json_document(body, MAX_MANIFEST_BYTES)
        try:
            return cls.model_validate_json(body)
        except ValidationError:
            raise ContractError("invalid topology manifest") from None

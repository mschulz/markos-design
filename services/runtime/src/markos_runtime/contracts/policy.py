"""Reviewed service policies and exact synthetic subscriptions, not live broker ACLs."""

from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from markos_runtime.contracts.base import (
    MAX_DOCUMENT_BYTES,
    ContractError,
    Domain,
    EventType,
    Name,
    ValueModel,
    parse_document,
)
from markos_runtime.contracts.events import TaskRequest
from markos_runtime.contracts.wire import RuntimeEvent, routing_key


class ServicePolicy(ValueModel):
    service_id: Name
    owner_id: UUID
    domain: Domain
    publishes: tuple[EventType, ...] = Field(default=(), max_length=1)
    consumes: tuple[EventType, ...] = Field(default=(), max_length=1)

    @model_validator(mode="after")
    def has_capability(self) -> Self:
        if not self.publishes and not self.consumes:
            raise ValueError("service has no capability")
        return self


class Subscription(ValueModel):
    service_id: Name
    owner_id: UUID
    domain: Domain
    event_type: EventType

    @property
    def queue(self) -> str:
        return f"markos.{self.domain}.{self.service_id}"

    @property
    def exchange(self) -> str:
        return f"markos.{self.domain}.events"

    @property
    def routing_key(self) -> str:
        return routing_key(self.event_type)


class Manifest(ValueModel):
    policies: tuple[ServicePolicy, ...] = Field(min_length=1, max_length=64)
    subscriptions: tuple[Subscription, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        keys = [p.service_id for p in self.policies]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate service identity")
        seen: set[tuple[str, str, str]] = set()
        for subscription in self.subscriptions:
            policy = self.policy(subscription.service_id)
            if (policy.owner_id, policy.domain) != (subscription.owner_id, subscription.domain):
                raise ValueError("subscription scope mismatch")
            if subscription.event_type not in policy.consumes:
                raise ValueError("subscription not granted")
            key = (subscription.service_id, subscription.domain, subscription.event_type)
            if key in seen:
                raise ValueError("duplicate subscription")
            seen.add(key)
        for policy in self.policies:
            for event_type in policy.consumes:
                if (policy.service_id, policy.domain, event_type) not in seen:
                    raise ValueError("missing required subscription")
        return self

    @classmethod
    def from_wire(cls, body: bytes) -> Self:
        return parse_document(cls, body, MAX_DOCUMENT_BYTES)

    def policy(self, service_id: str) -> ServicePolicy:
        for policy in self.policies:
            if policy.service_id == service_id:
                return policy
        raise ContractError("unknown service identity")

    def authorize_publish(self, event: RuntimeEvent, trusted_service_id: str) -> None:
        policy = self.policy(trusted_service_id)
        if (event.source, event.owner_id, event.domain) != (
            policy.service_id,
            policy.owner_id,
            policy.domain,
        ) or event.event_type not in policy.publishes:
            raise ContractError("publication not authorized")

    def task_request(
        self,
        event: RuntimeEvent,
        trusted_service_id: str,
        task_id: UUID,
        *,
        trusted_publisher_id: str,
    ) -> TaskRequest:
        self.authorize_publish(event, trusted_publisher_id)
        policy = self.policy(trusted_service_id)
        if (event.owner_id, event.domain) != (policy.owner_id, policy.domain) or (
            event.event_type not in policy.consumes
        ):
            raise ContractError("consumption not authorized")
        return TaskRequest(
            task_id=task_id,
            event_id=event.event_id,
            owner_id=event.owner_id,
            domain=event.domain,
            consumer_id=policy.service_id,
            event_type=event.event_type,
            operation_key=event.operation_key,
        )

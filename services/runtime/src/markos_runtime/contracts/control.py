"""M1-A data contracts only: parsing never authenticates or grants runtime authority."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, Field, field_validator, model_validator

from markos_runtime.contracts.base import (
    Domain,
    Name,
    OperationKey,
    ValueModel,
    parse_document,
)

HTTP_BYTES = 65_536
CHAT_BYTES = 16_384
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[int, Field(ge=1)]
Role = Literal["viewer", "operator", "approver", "administrator"]
Capability = Literal["inspect", "chat", "configure", "decide-approval"]


def utc(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timezone-aware UTC required")
    return value


def chat_text(value: str) -> str:
    if not value.strip() or len(value.encode("utf-8")) > CHAT_BYTES:
        raise ValueError("bounded nonblank UTF-8 text required")
    return value


Utc = Annotated[datetime, AfterValidator(utc)]
Text = Annotated[
    str,
    Field(min_length=1, max_length=CHAT_BYTES, json_schema_extra={"x-max-utf8-bytes": CHAT_BYTES}),
    AfterValidator(chat_text),
]


def digest_document(document: object) -> str:
    return hashlib.sha256(
        json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


class ControlValue(ValueModel):
    schema_version: Literal[1]

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("integer version required")
        return value

    @classmethod
    def from_wire(cls, body: bytes) -> Self:
        return parse_document(cls, body, HTTP_BYTES)

    def to_wire(self) -> bytes:
        body = self.model_dump_json().encode("utf-8")
        self.from_wire(body)
        return body


class ScopedValue(ControlValue):
    owner_id: UUID
    domain: Domain


class HumanPrincipal(ControlValue):
    """Future verifier output descriptor, never evidence that a JWT was verified."""

    principal_id: UUID
    issuer_id: Literal["synthetic-issuer"]
    subject: str = Field(min_length=1, max_length=256)
    session_id: UUID
    token_kind: Literal["human-access"]
    expires_at: Utc


class Membership(ScopedValue):
    principal_id: UUID
    revision: Revision
    roles: tuple[Role, ...] = Field(min_length=1, max_length=4)

    @field_validator("roles")
    @classmethod
    def distinct(cls, value: tuple[Role, ...]) -> tuple[Role, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate role")
        return value

    def supports(self, capability: Capability) -> bool:
        # Pure role matrix, NOT request authorization. Current DB membership/session is mandatory.
        required = {
            "inspect": {"viewer", "operator", "approver", "administrator"},
            "chat": {"operator"},
            "configure": {"administrator"},
            "decide-approval": {"approver"},
        }
        return bool(set(self.roles) & required.get(capability, set()))


class AgentRevision(ScopedValue):
    agent_id: Literal["control-test-agent"]
    revision: Revision
    description: str = Field(min_length=1, max_length=512)
    input_events: tuple[Literal["control.chat.requested"], ...] = ("control.chat.requested",)
    output_events: tuple[Literal["control.chat.completed"], ...] = ("control.chat.completed",)
    required_tools: tuple[Name, ...] = Field(default=(), max_length=0)
    model_policy: Literal["deterministic-no-model"] = "deterministic-no-model"
    initially_enabled: Literal[False] = False
    timeout_seconds: Literal[10] = 10
    retries: Literal[0] = 0

    @field_validator("timeout_seconds", "retries", mode="before")
    @classmethod
    def numeric_literals(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("integer required")
        return value

    @field_validator("initially_enabled", mode="before")
    @classmethod
    def disabled(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("boolean required")
        return value

    @model_validator(mode="after")
    def exact_events(self) -> Self:
        if self.input_events != ("control.chat.requested",) or self.output_events != (
            "control.chat.completed",
        ):
            raise ValueError("exact event declarations required")
        return self


class ConfigRevision(ScopedValue):
    revision: Revision
    previous_revision: int = Field(ge=0)
    agent: AgentRevision
    reason: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def scope_and_revision(self) -> Self:
        if (self.owner_id, self.domain) != (self.agent.owner_id, self.agent.domain):
            raise ValueError("configuration scope mismatch")
        if self.previous_revision != self.revision - 1:
            raise ValueError("contiguous revision required")
        return self


class AgentStateRequest(ControlValue):
    expected_revision: Revision
    enabled: bool
    reason: str = Field(min_length=1, max_length=256)


class Conversation(ScopedValue):
    conversation_id: UUID
    created_by: UUID
    created_at: Utc


class Message(ScopedValue):
    conversation_id: UUID
    message_id: UUID
    sequence: int = Field(ge=1)
    author_kind: Literal["human", "test-agent"]
    body: Text
    created_at: Utc
    in_reply_to: UUID | None

    @model_validator(mode="after")
    def reply(self) -> Self:
        if self.author_kind == "test-agent" and self.in_reply_to is None:
            raise ValueError("reply identity required")
        if self.in_reply_to == self.message_id:
            raise ValueError("self reply forbidden")
        return self


class ChatRequest(ControlValue):
    """HTTP input has no actor, owner, role, tool or secret fields."""

    domain: Domain
    conversation_id: UUID
    operation_key: OperationKey
    agent_id: Literal["control-test-agent"]
    expected_agent_revision: Revision
    text: Text


class MessageReference(ScopedValue):
    conversation_id: UUID
    message_id: UUID
    sha256: Digest
    kind: Literal["human-message", "assistant-message"]


class ChatEvent(ScopedValue):
    """Future wire contract. M0 transport/store still reject these event types."""

    event_id: UUID
    event_type: Literal["control.chat.requested", "control.chat.completed"]
    source: Literal["control-gateway", "control-test-agent"]
    occurred_at: Utc
    correlation_id: UUID
    causation_id: UUID | None
    trace_id: UUID
    sensitivity: Literal["internal"] = "internal"
    operation_key: OperationKey
    message: MessageReference
    expires_at: Utc | None = None

    @model_validator(mode="after")
    def binding(self) -> Self:
        if self.expires_at is not None and self.expires_at <= self.occurred_at:
            raise ValueError("invalid event expiry")
        request = self.event_type == "control.chat.requested"
        if (self.owner_id, self.domain) != (self.message.owner_id, self.message.domain):
            raise ValueError("message scope mismatch")
        if self.source != ("control-gateway" if request else "control-test-agent"):
            raise ValueError("producer mismatch")
        if self.message.kind != ("human-message" if request else "assistant-message"):
            raise ValueError("reference kind mismatch")
        if not request and self.causation_id is None:
            raise ValueError("completion causation required")
        return self

    @property
    def routing_key(self) -> str:
        return (
            "v1.control.chat-requested"
            if self.event_type == "control.chat.requested"
            else "v1.control.chat-completed"
        )

    @property
    def consumers(self) -> tuple[str, ...]:
        return (
            ("control-test-agent",)
            if self.event_type == "control.chat.requested"
            else ("control-activity",)
        )


class Activity(ScopedValue):
    activity_id: UUID
    sequence: int = Field(ge=1)
    correlation_id: UUID
    conversation_id: UUID | None
    recorded_at: Utc
    kind: Literal["queued", "working", "awaiting-decision", "succeeded", "failed", "unknown"]
    object_id: UUID


class FixtureArguments(ControlValue):
    label: str = Field(min_length=1, max_length=256)


class ToolGrant(ScopedValue):
    grant_id: UUID
    revision: Revision
    agent_id: Literal["control-test-agent"]
    server_id: Literal["synthetic-mcp-fixture"]
    tool: Literal["fixture.read"]
    tool_schema_sha256: Digest
    data_class: Literal["synthetic"] = "synthetic"
    recipient: Literal["synthetic-mcp-fixture"] = "synthetic-mcp-fixture"
    effect: Literal["read"] = "read"
    enabled: Literal[False] = False

    @field_validator("enabled", mode="before")
    @classmethod
    def disabled(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("boolean required")
        return value


class CallIntent(ScopedValue):
    """Synthetic data only; digest does not replace authorization/atomic DB consumption."""

    intent_id: UUID
    principal_id: UUID
    agent_id: Literal["control-test-agent"]
    agent_revision: Revision
    task_id: UUID
    run_id: UUID
    epoch: UUID
    task_fence: Revision
    operation_key: OperationKey
    server_id: Literal["synthetic-mcp-fixture"]
    tool: Literal["fixture.read"]
    tool_schema_sha256: Digest
    grant_id: UUID
    grant_revision: Revision
    policy_revision: Revision
    arguments: FixtureArguments
    arguments_sha256: Digest
    preview: str = Field(min_length=1, max_length=1024)
    preview_sha256: Digest
    recipient: Literal["synthetic-mcp-fixture"]
    data_class: Literal["synthetic"]
    effect: Literal["read"]
    created_at: Utc
    expires_at: Utc
    max_attempts: Literal[1] = 1

    @field_validator("max_attempts", mode="before")
    @classmethod
    def exact_attempts(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("integer required")
        return value

    @model_validator(mode="after")
    def bindings(self) -> Self:
        if digest_document(self.arguments.model_dump(mode="json")) != self.arguments_sha256:
            raise ValueError("argument digest mismatch")
        if hashlib.sha256(self.preview.encode("utf-8")).hexdigest() != self.preview_sha256:
            raise ValueError("preview digest mismatch")
        if not timedelta(0) < self.expires_at - self.created_at <= timedelta(minutes=15):
            raise ValueError("bounded approval expiry required")
        return self

    def intent_sha256(self) -> str:
        # Revalidate constructed/copied values before producing a stable binding.
        validated = self.from_wire(self.to_wire())
        return digest_document(validated.model_dump(mode="json"))


class ApprovalDecisionRequest(ControlValue):
    """Human/scope comes from future verified context, never this request body."""

    intent_id: UUID
    intent_sha256: Digest
    expected_revision: Revision
    decision: Literal["approve", "reject", "revoke"]


class ApprovalRecord(ScopedValue):
    approval_id: UUID
    intent_id: UUID
    intent_sha256: Digest
    revision: Revision
    status: Literal["awaiting", "approved", "rejected", "expired", "revoked", "consumed"]
    approver_id: UUID | None
    dispatch_id: UUID | None
    expires_at: Utc

    @model_validator(mode="after")
    def consumption(self) -> Self:
        if (self.status == "consumed") != (self.dispatch_id is not None):
            raise ValueError("consumption dispatch binding required")
        if (
            self.status in {"approved", "rejected", "revoked", "consumed"}
            and self.approver_id is None
        ):
            raise ValueError("human decision identity required")
        return self


class AuditRecord(ScopedValue):
    audit_id: UUID
    principal_id: UUID
    correlation_id: UUID
    object_id: UUID
    intent_sha256: Digest | None
    recorded_at: Utc
    outcome: Literal[
        "requested",
        "denied",
        "awaiting",
        "approved",
        "rejected",
        "expired",
        "revoked",
        "dispatched",
        "succeeded",
        "failed",
        "unknown",
    ]
    reason: Literal[
        "policy", "scope", "expired", "revoked", "budget", "accepted", "dependency", "unknown"
    ]


class ApiError(ControlValue):
    request_id: UUID
    code: Literal[
        "unauthenticated",
        "forbidden",
        "not-found",
        "conflict",
        "too-large",
        "limited",
        "unavailable",
    ]

    @property
    def status(self) -> int:
        return {
            "unauthenticated": 401,
            "forbidden": 403,
            "not-found": 404,
            "conflict": 409,
            "too-large": 413,
            "limited": 429,
            "unavailable": 503,
        }[self.code]

"""Closed synthetic tool profile. Descriptors never grant execution authority."""

from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from markos_runtime.contracts.control import (
    CallIntent,
    ChatRequest,
    ControlValue,
    Digest,
    FixtureArguments,
    ScopedValue,
    Utc,
    digest_document,
)

PROTOCOL = "2026-07-28"
INPUT_SCHEMA = {
    "type": "object",
    "properties": {"label": {"type": "string", "minLength": 1, "maxLength": 256}},
    "required": ["label"],
    "additionalProperties": False,
}
SCHEMA_SHA256 = digest_document(INPUT_SCHEMA)


def fixture_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "127.0.0.1"
        or not parsed.port
        or not 1024 <= parsed.port <= 65535
        or parsed.netloc != f"127.0.0.1:{parsed.port}"
        or parsed.path != "/mcp"
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError("explicit numeric loopback HTTPS fixture endpoint required")
    return value


class ReviewedFixtureGrant(ScopedValue):
    grant_id: UUID
    revision: int = Field(ge=1)
    previous_revision: int = Field(ge=0)
    agent_id: Literal["control-test-agent"] = "control-test-agent"
    server_id: Literal["synthetic-mcp-fixture"] = "synthetic-mcp-fixture"
    endpoint: str
    protocol: Literal["2026-07-28"] = "2026-07-28"
    tool: Literal["fixture.read"] = "fixture.read"
    tool_schema_sha256: Digest = SCHEMA_SHA256
    data_class: Literal["synthetic"] = "synthetic"
    recipient: Literal["synthetic-mcp-fixture"] = "synthetic-mcp-fixture"
    effect: Literal["read"] = "read"
    require_approval: bool = True
    enabled: bool = False
    reason: str = Field(min_length=1, max_length=256)

    _endpoint = field_validator("endpoint")(fixture_endpoint)

    @model_validator(mode="after")
    def exact_profile(self) -> Self:
        if self.revision != self.previous_revision + 1 or self.tool_schema_sha256 != SCHEMA_SHA256:
            raise ValueError("contiguous reviewed fixed-schema grant required")
        return self


class GovernedChatRequest(ChatRequest):
    fixture_arguments: FixtureArguments


class ReconciliationRequest(ControlValue):
    dispatch_id: UUID
    expected_revision: int = Field(ge=1)
    outcome: Literal["observed-succeeded", "not-observed", "unresolved"]
    evidence: Literal["synthetic-ledger-reviewed"]


class FixtureResult(ScopedValue):
    kind: Literal["synthetic"]
    label: str = Field(min_length=1, max_length=256)


class GovernedApproval(ScopedValue):
    """Public durable view; standing grants and automatic revocation have explicit semantics."""

    approval_id: UUID
    intent_id: UUID
    intent_sha256: Digest
    revision: int = Field(ge=1)
    status: Literal["awaiting", "approved", "rejected", "expired", "revoked", "consumed"]
    approver_id: UUID | None
    dispatch_id: UUID | None
    requires_human: bool
    cancellation_reason: (
        Literal[
            "test-service-check-failed",
            "requester-signed-out",
            "sign-in-expired",
            "access-changed",
            "settings-changed",
            "preview-restored",
            "request-cancelled",
            "approval-withdrawn",
            "unknown",
        ]
        | None
    ) = None
    expires_at: Utc
    intent: CallIntent | None
    endpoint: str

    _endpoint = field_validator("endpoint")(fixture_endpoint)

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.cancellation_reason is not None and self.status != "revoked":
            raise ValueError("cancellation reason requires cancelled approval")
        if self.approval_id != self.intent_id:
            raise ValueError("exact approval identity required")
        if self.intent is not None and self.intent.intent_sha256() != self.intent_sha256:
            raise ValueError("immutable preview mismatch")
        if self.status == "consumed" and self.dispatch_id is None:
            raise ValueError("durable consumption reference required")
        if (
            self.requires_human
            and self.status in {"approved", "consumed"}
            and self.approver_id is None
        ):
            raise ValueError("human decision required by reviewed grant")
        return self


class GovernedDispatch(ScopedValue):
    dispatch_id: UUID
    operation_id: UUID
    intent_id: UUID
    state: Literal["dispatched", "succeeded", "failed", "unknown", "resolved"]
    revision: int = Field(ge=1)
    started_at: Utc
    finished_at: Utc | None
    result_sha256: Digest | None
    result: FixtureResult | None


class GovernanceAudit(ScopedValue):
    sequence: int = Field(ge=1)
    audit_id: UUID
    principal_id: UUID
    correlation_id: UUID
    intent_id: UUID | None
    dispatch_id: UUID | None
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
        "reconciled",
        "cleanup",
    ]
    reason: str = Field(min_length=1, max_length=64)
    metadata: dict[str, str | int | None]

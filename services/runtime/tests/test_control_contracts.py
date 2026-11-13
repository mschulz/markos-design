import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

from markos_runtime.contracts.base import ContractError, parse_document
from markos_runtime.contracts.control import (
    CHAT_BYTES,
    AgentRevision,
    AgentStateRequest,
    ApiError,
    ApprovalDecisionRequest,
    ApprovalRecord,
    CallIntent,
    ChatEvent,
    ChatRequest,
    ControlValue,
    HumanPrincipal,
    Membership,
    Message,
    digest_document,
)
from markos_runtime.contracts.events import EventEnvelope
from markos_runtime.contracts.governance import GovernedChatRequest, ReconciliationRequest
from markos_runtime.control.config import ControlConfig, ControlLimits
from markos_runtime.schemas import artifacts, models

EXAMPLES = Path(__file__).resolve().parents[3] / "config/runtime/control"


def document(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / (name + ".example.json")).read_bytes())  # type: ignore[no-any-return]


def parse(model: type[ControlValue], value: dict[str, Any]) -> ControlValue:
    return model.from_wire(json.dumps(value, ensure_ascii=False).encode())


@pytest.mark.parametrize(
    "name",
    sorted(
        p.stem.removesuffix(".example")
        for p in EXAMPLES.glob("*.example.json")
        if p.name != "issuer-public.example.json"
    ),
)
def test_control_examples_validate_against_generated_schemas(name: str) -> None:
    model = models()["control-" + name]
    parse_document(model, (EXAMPLES / (name + ".example.json")).read_bytes(), 65536)
    schema = json.loads(artifacts()["control-" + name + ".schema.json"])
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(document(name))


@pytest.mark.parametrize(
    "model,name",
    [
        (ChatRequest, "chat-request"),
        (ApprovalDecisionRequest, "approval-decision"),
        (GovernedChatRequest, "governed-chat-request"),
        (ReconciliationRequest, "reconciliation"),
    ],
)
@pytest.mark.parametrize(
    "field", ["owner_id", "actor_id", "roles", "verified", "token", "secret", "tool", "server_url"]
)
def test_http_inputs_cannot_claim_identity_authority_or_secret(
    model: type[ControlValue], name: str, field: str
) -> None:
    value = document(name)
    value[field] = "forged"
    with pytest.raises(ContractError):
        parse(model, value)


@pytest.mark.parametrize("bad", [True, 1.0, "1", 2, None])
def test_exact_schema_version(bad: object) -> None:
    value = document("chat-request")
    value["schema_version"] = bad
    with pytest.raises(ContractError):
        parse(ChatRequest, value)


@pytest.mark.parametrize(
    "text,accepted",
    [
        ("a" * CHAT_BYTES, True),
        ("a" * (CHAT_BYTES + 1), False),
        ("é" * (CHAT_BYTES // 2), True),
        ("é" * (CHAT_BYTES // 2) + "a", False),
        ("  ", False),
    ],
)
def test_chat_utf8_exact_byte_boundary(text: str, accepted: bool) -> None:
    value = document("chat-request")
    value["text"] = text
    if accepted:
        assert parse(ChatRequest, value).to_wire()
    else:
        with pytest.raises(ContractError):
            parse(ChatRequest, value)


def test_duplicate_nonstandard_and_document_boundaries() -> None:
    raw = (EXAMPLES / "chat-request.example.json").read_bytes()
    for bad in [b'{"schema_version":1,"schema_version":1}', b'{"text":NaN}', raw + b" " * 65536]:
        with pytest.raises(ContractError):
            ChatRequest.from_wire(bad)
    exact = raw + b" " * (65536 - len(raw))
    assert ChatRequest.from_wire(exact)
    with pytest.raises(ContractError):
        ChatRequest.from_wire(exact + b" ")


@pytest.mark.parametrize(
    "field,bad",
    [
        ("owner_id", "00000000-0000-0000-0000-000000000000"),
        ("domain", "unknown"),
        ("source", "model"),
        ("schema_version", True),
        ("occurred_at", "2026-11-12T00:00:00"),
        ("occurred_at", "2026-11-12T01:00:00+01:00"),
        ("expires_at", "2026-11-11T00:00:00Z"),
    ],
)
def test_event_rejects_bad_scope_type_and_clock(field: str, bad: object) -> None:
    value = document("chat-event")
    value[field] = bad
    with pytest.raises(ContractError):
        parse(ChatEvent, value)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("domain", "work"),
        ("owner_id", "99999999-9999-4999-8999-999999999999"),
        ("kind", "assistant-message"),
    ],
)
def test_chat_reference_scope_and_kind_must_match(field: str, bad: str) -> None:
    value = document("chat-event")
    value["message"][field] = bad
    with pytest.raises(ContractError):
        parse(ChatEvent, value)


def test_planned_chat_bindings_do_not_enable_m0_transport() -> None:
    value = document("chat-event")
    request = ChatEvent.from_wire(json.dumps(value).encode())
    assert request.routing_key == "v1.control.chat-requested" and request.consumers == (
        "control-test-agent",
    )
    with pytest.raises(ContractError):
        EventEnvelope.from_wire(request.to_wire())
    value.update(
        event_type="control.chat.completed",
        source="control-test-agent",
        causation_id=value["event_id"],
    )
    value["message"]["kind"] = "assistant-message"
    complete = ChatEvent.from_wire(json.dumps(value).encode())
    assert complete.routing_key == "v1.control.chat-completed" and complete.consumers == (
        "control-activity",
    )
    value["causation_id"] = None
    with pytest.raises(ContractError):
        parse(ChatEvent, value)


@pytest.mark.parametrize(
    "role,allowed",
    [
        ("viewer", {"inspect"}),
        ("operator", {"inspect", "chat"}),
        ("approver", {"inspect", "decide-approval"}),
        ("administrator", {"inspect", "configure"}),
    ],
)
def test_explicit_nonhierarchical_role_matrix(role: str, allowed: set[str]) -> None:
    value = document("membership")
    value["roles"] = [role]
    membership = Membership.from_wire(json.dumps(value).encode())
    for capability in ("inspect", "chat", "configure", "decide-approval"):
        assert membership.supports(capability) == (capability in allowed)
    value["roles"] = [role, role]
    with pytest.raises(ContractError):
        parse(Membership, value)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("roles", ["root"]),
        ("roles", []),
        ("revision", 0),
        ("principal_id", "00000000-0000-0000-0000-000000000000"),
    ],
)
def test_membership_rejects_invalid_descriptors(field: str, bad: object) -> None:
    value = document("membership")
    value[field] = bad
    with pytest.raises(ContractError):
        parse(Membership, value)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("initially_enabled", True),
        ("initially_enabled", 0),
        ("model_policy", "llm"),
        ("required_tools", ["shell"]),
        ("input_events", []),
        ("input_events", ["control.chat.requested"] * 2),
        ("timeout_seconds", True),
        ("retries", 1),
    ],
)
def test_agent_starts_disabled_and_has_no_tools_or_general_executor(
    field: str, bad: object
) -> None:
    value = document("agent")
    value[field] = bad
    with pytest.raises(ContractError):
        parse(AgentRevision, value)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("arguments_sha256", "f" * 64),
        ("preview_sha256", "f" * 64),
        ("expires_at", "2026-11-12T00:15:01Z"),
        ("expires_at", "2026-11-12T00:00:00Z"),
        ("max_attempts", True),
        ("server_id", "other"),
        ("tool", "fixture.write"),
        ("effect", "mutation"),
    ],
)
def test_immutable_intent_binding_and_fixture_only_boundary(field: str, bad: object) -> None:
    value = document("intent")
    value[field] = bad
    with pytest.raises(ContractError):
        parse(CallIntent, value)


def test_intent_digest_is_stable_and_changes_with_each_authority_binding() -> None:
    value = document("intent")
    initial = CallIntent.from_wire(json.dumps(value).encode()).intent_sha256()
    assert (
        initial
        == CallIntent.from_wire(
            json.dumps(value, sort_keys=True, indent=4).encode()
        ).intent_sha256()
    )
    for field in ["agent_revision", "grant_revision", "policy_revision", "task_fence"]:
        changed = {**value, field: 2}
        assert CallIntent.from_wire(json.dumps(changed).encode()).intent_sha256() != initial
    changed = {**value, "arguments": {"schema_version": 1, "label": "Different synthetic read"}}
    with pytest.raises(ContractError):
        parse(CallIntent, changed)
    changed["arguments_sha256"] = digest_document(changed["arguments"])
    assert CallIntent.from_wire(json.dumps(changed).encode()).intent_sha256() != initial
    changed = {**value, "preview": "Different preview"}
    changed["preview_sha256"] = hashlib.sha256(changed["preview"].encode()).hexdigest()
    assert CallIntent.from_wire(json.dumps(changed).encode()).intent_sha256() != initial


def test_unsafe_constructed_intent_is_revalidated_before_hashing() -> None:
    intent = CallIntent.from_wire(json.dumps(document("intent")).encode())
    forged = intent.model_copy(update={"arguments_sha256": "f" * 64})
    with pytest.raises(ContractError):
        forged.intent_sha256()


@pytest.mark.parametrize(
    "state,dispatch,approver",
    [
        ("consumed", None, None),
        ("approved", None, None),
        ("awaiting", "11111111-1111-4111-8111-111111111111", None),
    ],
)
def test_approval_consumption_and_human_identity_bindings(
    state: str, dispatch: str | None, approver: str | None
) -> None:
    value = document("approval")
    value.update(status=state, dispatch_id=dispatch, approver_id=approver)
    with pytest.raises(ContractError):
        parse(ApprovalRecord, value)


def test_error_has_only_stable_code_and_request_id() -> None:
    value = document("error")
    error = ApiError.from_wire(json.dumps(value).encode())
    assert error.status == 403 and not hasattr(error, "message")
    value["detail"] = "credential or arbitrary exception"
    with pytest.raises(ContractError):
        parse(ApiError, value)


def test_nonhuman_principal_and_invalid_message_reply_are_rejected() -> None:
    value = document("principal")
    value["token_kind"] = "service-access"
    with pytest.raises(ContractError):
        parse(HumanPrincipal, value)
    value = document("message")
    value.update(author_kind="test-agent", in_reply_to=None)
    with pytest.raises(ContractError):
        parse(Message, value)
    state = document("agent-state")
    state["enabled"] = 1
    with pytest.raises(ContractError):
        parse(AgentStateRequest, state)


@pytest.mark.parametrize(
    "field",
    [
        "gateway_enabled",
        "chat_execution_enabled",
        "tool_execution_enabled",
        "real_accounts_enabled",
    ],
)
@pytest.mark.parametrize("bad", [True, 0, "false"])
def test_control_configuration_cannot_enable_later_slices(field: str, bad: object) -> None:
    value = document("config")
    value[field] = bad
    with pytest.raises(ContractError):
        parse(ControlConfig, value)


@pytest.mark.parametrize(
    "bad",
    [
        "https://real.example",
        "http://auth.markos.test",
        "https://auth.markos.test/?key=secret",
        "https://user:secret@auth.markos.test",
        "https://auth.markos.test.evil",
    ],
)
def test_issuer_allowlist_is_explicit_and_no_network_is_implied(bad: str) -> None:
    value = document("config")
    value["issuer_url"] = bad
    with pytest.raises(ContractError):
        parse(ControlConfig, value)


@pytest.mark.parametrize("field", sorted(set(ControlLimits.model_fields) - {"schema_version"}))
def test_each_frozen_limit_rejects_one_over_and_boolean(field: str) -> None:
    value = document("limits")
    assert parse(ControlLimits, value)
    for bad in [value[field] + 1, True, float(value[field])]:
        with pytest.raises(ContractError):
            parse(ControlLimits, {**value, field: bad})


def test_missing_schema_version_and_unknown_issuer_are_rejected() -> None:
    value = document("principal")
    value["issuer_id"] = "untrusted-issuer"
    with pytest.raises(ContractError):
        parse(HumanPrincipal, value)
    cases: list[tuple[type[ControlValue], str]] = [
        (ChatRequest, "chat-request"),
        (ControlConfig, "config"),
    ]
    for model, name in cases:
        value = document(name)
        del value["schema_version"]
        with pytest.raises(ContractError):
            parse(model, value)


@pytest.mark.parametrize("field", ["revision", "domain", "agent"])
def test_revision_and_nested_scope_are_consistent(field: str) -> None:
    from markos_runtime.contracts.control import ConfigRevision

    value = document("revision")
    if field == "revision":
        value["revision"] = 2
    elif field == "domain":
        value["domain"] = "work"
    else:
        value["agent"]["owner_id"] = "99999999-9999-4999-8999-999999999999"
    with pytest.raises(ContractError):
        parse(ConfigRevision, value)


def test_disabled_grant_cannot_be_enabled_or_coerced() -> None:
    from markos_runtime.contracts.control import ToolGrant

    value = document("tool-grant")
    for bad in [True, 0, "false"]:
        with pytest.raises(ContractError):
            parse(ToolGrant, {**value, "enabled": bad})

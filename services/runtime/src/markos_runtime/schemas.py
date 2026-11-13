"""Generate/check schemas only at an explicitly supplied output directory."""

import argparse
import json
from pathlib import Path

from markos_runtime.config import RuntimeConfig
from markos_runtime.contracts.base import ValueModel
from markos_runtime.contracts.control import (
    Activity,
    AgentRevision,
    AgentStateRequest,
    ApiError,
    ApprovalDecisionRequest,
    ApprovalRecord,
    AuditRecord,
    CallIntent,
    ChatAdmission,
    ChatEvent,
    ChatRequest,
    ChatStatus,
    ConfigRevision,
    Conversation,
    ConversationRequest,
    HumanPrincipal,
    Membership,
    Message,
    ToolGrant,
)
from markos_runtime.contracts.events import EventEnvelope, TaskRequest
from markos_runtime.contracts.governance import (
    FixtureResult,
    GovernanceAudit,
    GovernedApproval,
    GovernedChatRequest,
    GovernedDispatch,
    ReconciliationRequest,
    ReviewedFixtureGrant,
)
from markos_runtime.contracts.policy import Manifest
from markos_runtime.contracts.replay import ReplayRequest
from markos_runtime.contracts.review import ReviewReference
from markos_runtime.contracts.schedules import ScheduleDefinition
from markos_runtime.control.api_config import (
    SyntheticApiConfig,
    SyntheticConversationApiConfig,
    SyntheticGovernanceApiConfig,
    SyntheticWebApiConfig,
)
from markos_runtime.control.config import ControlConfig, ControlLimits
from markos_runtime.lifecycle.config import ServiceConfig


def models() -> dict[str, type[ValueModel]]:
    return {
        "control-api": SyntheticApiConfig,
        "control-governed-approval": GovernedApproval,
        "control-governed-dispatch": GovernedDispatch,
        "control-governance-audit": GovernanceAudit,
        "control-governance-api": SyntheticGovernanceApiConfig,
        "control-reviewed-fixture-grant": ReviewedFixtureGrant,
        "control-governed-chat-request": GovernedChatRequest,
        "control-fixture-result": FixtureResult,
        "control-reconciliation": ReconciliationRequest,
        "control-conversation-api": SyntheticConversationApiConfig,
        "control-web-api": SyntheticWebApiConfig,
        "control-conversation-request": ConversationRequest,
        "control-chat-admission": ChatAdmission,
        "control-chat-status": ChatStatus,
        "control-config": ControlConfig,
        "control-limits": ControlLimits,
        "control-principal": HumanPrincipal,
        "control-membership": Membership,
        "control-agent": AgentRevision,
        "control-agent-state": AgentStateRequest,
        "control-revision": ConfigRevision,
        "control-conversation": Conversation,
        "control-message": Message,
        "control-chat-request": ChatRequest,
        "control-chat-event": ChatEvent,
        "control-activity": Activity,
        "control-tool-grant": ToolGrant,
        "control-intent": CallIntent,
        "control-approval-decision": ApprovalDecisionRequest,
        "control-approval": ApprovalRecord,
        "control-audit": AuditRecord,
        "control-error": ApiError,
        "service": ServiceConfig,
        "replay": ReplayRequest,
        "schedule": ScheduleDefinition,
        "review": ReviewReference,
        "event": EventEnvelope,
        "task": TaskRequest,
        "manifest": Manifest,
        "runtime-config": RuntimeConfig,
    }


def artifacts() -> dict[str, str]:
    return {
        f"{name}.schema.json": json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                **model.model_json_schema(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
        for name, model in models().items()
    }


def check(directory: Path) -> bool:
    expected = artifacts()
    if not directory.is_dir() or {p.name for p in directory.glob("*.schema.json")} != set(expected):
        return False
    return all((directory / name).read_text() == body for name, body in expected.items())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--write", action="store_true", help="Explicitly regenerate artifacts")
    args = parser.parse_args()
    if args.write:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, body in artifacts().items():
            (args.output_dir / name).write_text(body)
    elif not check(args.output_dir):
        print("Runtime schema drift detected")
        return 1
    print("Runtime schemas valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

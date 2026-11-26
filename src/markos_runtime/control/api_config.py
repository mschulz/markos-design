"""Synthetic API profiles, separate from the inert control preflight configuration."""

from typing import Literal

from pydantic import field_validator

from markos_runtime.contracts.base import loopback_https
from markos_runtime.contracts.control import ControlValue


class _ApiProfile(ControlValue):
    environment: Literal["test"] = "test"
    identity_profile: Literal["synthetic-pkce"] = "synthetic-pkce"
    api_origin: str
    real_accounts_enabled: Literal[False] = False
    tool_execution_enabled: Literal[False] = False

    @field_validator("api_origin")
    @classmethod
    def private_origin(cls, value: str) -> str:
        loopback_https(
            value,
            paths=frozenset({""}),
            hosts=frozenset({"127.0.0.1", "localhost", "::1"}),
            exact=False,
        )
        return value

    @field_validator("real_accounts_enabled", "tool_execution_enabled", mode="before")
    @classmethod
    def disabled(cls, value: object) -> object:
        if type(value) is not bool or value is not False:
            raise ValueError("disabled boolean required")
        return value


class SyntheticApiConfig(_ApiProfile):
    """Base synthetic API profile; chat execution is always disabled."""

    chat_execution_enabled: Literal[False] = False

    @field_validator("chat_execution_enabled", mode="before")
    @classmethod
    def no_chat(cls, value: object) -> object:
        if type(value) is not bool or value is not False:
            raise ValueError("disabled boolean required")
        return value


class SyntheticConversationApiConfig(_ApiProfile):
    """Conversation API profile; built from config, never switched on over HTTP."""

    conversation_profile: Literal["deterministic-conversation-v1"]
    chat_execution_enabled: Literal[True] = True

    @field_validator("chat_execution_enabled", mode="before")
    @classmethod
    def fixed_chat(cls, value: object) -> object:
        if type(value) is not bool or value is not True:
            raise ValueError("fixed synthetic conversation switch required")
        return value


class SyntheticWebApiConfig(SyntheticConversationApiConfig):
    """Local fixture web UI profile; enables no production use or tools."""

    web_profile: Literal["synthetic-web-cli-v1"]


class SyntheticGovernanceApiConfig(SyntheticWebApiConfig):
    """Separately selected fixed synthetic fixture path; general tool execution stays false."""

    governance_profile: Literal["synthetic-governance-v1"]


class SyntheticBillApiConfig(SyntheticGovernanceApiConfig):
    """Explicit local bill-text workflow; no account, model or real-tool enablement."""

    bill_profile: Literal["bill-text-v1"]

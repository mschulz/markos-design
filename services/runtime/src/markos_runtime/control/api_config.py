"""Separate synthetic API profile; M1-A preflight configuration stays inert."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import field_validator

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
        u = urlsplit(value)
        if (
            u.scheme != "https"
            or u.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not u.port
            or not 1024 <= u.port <= 65535
            or u.username
            or u.password
            or u.path
            or u.query
            or u.fragment
        ):
            raise ValueError("explicit loopback HTTPS origin required")
        return value

    @field_validator("real_accounts_enabled", "tool_execution_enabled", mode="before")
    @classmethod
    def disabled(cls, value: object) -> object:
        if type(value) is not bool or value is not False:
            raise ValueError("disabled boolean required")
        return value


class SyntheticApiConfig(_ApiProfile):
    """M1-C remains execution-disabled."""

    chat_execution_enabled: Literal[False] = False

    @field_validator("chat_execution_enabled", mode="before")
    @classmethod
    def no_chat(cls, value: object) -> object:
        if type(value) is not bool or value is not False:
            raise ValueError("disabled boolean required")
        return value


class SyntheticConversationApiConfig(_ApiProfile):
    """Separately constructed M1-D profile, never an HTTP-writable execution switch."""

    conversation_profile: Literal["deterministic-conversation-v1"]
    chat_execution_enabled: Literal[True] = True

    @field_validator("chat_execution_enabled", mode="before")
    @classmethod
    def fixed_chat(cls, value: object) -> object:
        if type(value) is not bool or value is not True:
            raise ValueError("fixed synthetic conversation switch required")
        return value

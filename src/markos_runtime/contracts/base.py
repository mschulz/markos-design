"""Strict value objects and bounded, redacted JSON parsing."""

import json
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

Domain = Literal["personal", "research", "work"]
EventType = Literal["foundation.noop.requested", "control.chat.requested", "control.chat.completed"]
Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:[-.][a-z0-9]+)*$", max_length=64)]
OperationKey = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$", max_length=128)]
MAX_EVENT_BYTES = 65_536
MAX_DOCUMENT_BYTES = 262_144


class ContractError(ValueError):
    """A stable public error containing no input, paths or credentials."""


def loopback_https(
    value: str,
    *,
    paths: frozenset[str],
    hosts: frozenset[str] = frozenset({"127.0.0.1"}),
    exact: bool = True,
) -> int:
    """Return the port of an HTTPS URL on a loopback host with no credentials or query.

    `exact` also requires the literal `127.0.0.1:<port>` form, so no other spelling
    of the same address can pass.
    """
    u = urlsplit(value)
    if (
        u.scheme != "https"
        or u.hostname not in hosts
        or not u.port
        or not 1024 <= u.port <= 65535
        or (exact and u.netloc != f"127.0.0.1:{u.port}")
        or u.username
        or u.password
        or u.path not in paths
        or u.query
        or u.fragment
    ):
        raise ValueError("loopback HTTPS endpoint required")
    return u.port


class ValueModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        validate_default=True,
        hide_input_in_errors=True,
        allow_inf_nan=False,
    )

    @field_validator("*", mode="after")
    @classmethod
    def reject_nil_uuid(cls, value: object) -> object:
        if isinstance(value, UUID) and not value.int:
            raise ValueError("nil identifiers are forbidden")
        return value


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError("non-JSON constant")


def parse_document[M: ValueModel](model: type[M], body: bytes, limit: int) -> M:
    if len(body) > limit:
        raise ContractError("document exceeds byte limit")
    try:
        # Pydantic JSON mode accepts wire UUID/time strings, not Python coercions.
        # Pre-scan rejects duplicate keys and nonstandard NaN/Infinity at every depth.
        json.loads(body.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
        return model.model_validate_json(body)
    except (ValueError, UnicodeError, RecursionError, ValidationError):
        raise ContractError("invalid contract document") from None

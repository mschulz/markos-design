"""Deprecated terminology adapters; application logic uses Markdown root only."""

import warnings
from collections.abc import Callable
from functools import wraps
from typing import Any, cast


def warn_renamed(old: str, new: str) -> None:
    warnings.warn(f"{old} is deprecated; use {new}", DeprecationWarning, stacklevel=3)


def legacy_keywords[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Accept the former root keyword for callers migrating to the canonical API."""

    @wraps(function)
    def call(*args: P.args, **kwargs: P.kwargs) -> R:
        translated: dict[str, Any] = dict(kwargs)
        if "vault_path" in translated:
            if "markdown_root" in translated:
                raise TypeError("provide markdown_root only, not both root names")
            warn_renamed("vault_path", "markdown_root")
            translated["markdown_root"] = translated.pop("vault_path")
        return cast(Callable[..., R], function)(*args, **translated)

    return call


def renamed_alias[**P, R](function: Callable[P, R], old_name: str) -> Callable[P, R]:
    @wraps(function)
    def call(*args: P.args, **kwargs: P.kwargs) -> R:
        warn_renamed(old_name, function.__name__)
        return function(*args, **kwargs)

    return call

"""Private session-file and redacted bounded client parsing; no ambient credentials."""

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from markos_runtime.client.cli import main
from markos_runtime.client.http import ClientError, SessionFile, document, parse_json


def value() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "api_origin": "https://127.0.0.1:18443",
        "bearer": str(uuid4()) + "." + "s" * 43,
        "expires_at": 2000000000,
    }


@pytest.mark.parametrize(
    "body",
    [
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1e309}',
        b'{"x":{"key":1,"key":2}}',
        b'[{"key":1,"key":2}]',
        b"bad",
        b"\xff",
    ],
)
def test_client_rejects_ambiguous_or_nonfinite_response(body: bytes) -> None:
    with pytest.raises(ClientError):
        parse_json(body)


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"bearer": "provider.jwt.token"},
        {"bearer": "00000000-0000-0000-0000-000000000000." + "s" * 43},
        {"expires_at": True},
        {"expires_at": "never"},
        {"extra": "credential"},
    ],
)
def test_invalid_credentials_never_reach_session_file(
    tmp_path: Path, change: dict[str, Any]
) -> None:
    path = tmp_path / "session.json"
    with pytest.raises(ClientError):
        SessionFile(path).write({**value(), **change})
    assert not path.exists()


@pytest.mark.parametrize("kind", ["mode", "git", "symlink", "fifo", "relative", "directory"])
def test_session_file_rejects_unsafe_destination_and_source(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "session.json"
    if kind == "relative":
        with pytest.raises(ClientError):
            SessionFile(Path("session.json"))
        return
    if kind == "git":
        (tmp_path / ".git").mkdir()
        with pytest.raises(ClientError):
            SessionFile(path)
        return
    store = SessionFile(path)
    if kind == "symlink":
        target = tmp_path / "target.json"
        target.write_text(json.dumps(value()))
        path.symlink_to(target)
        with pytest.raises(ClientError):
            SessionFile(path)
    elif kind == "fifo":
        os.mkfifo(path)
        with pytest.raises(ClientError):
            store.read()
    elif kind == "directory":
        path.mkdir()
        with pytest.raises(ClientError):
            store.read()
    else:
        store.write(value())
        path.chmod(0o644)
        with pytest.raises(ClientError):
            store.read()


def test_private_store_atomic_replace_and_explicit_removal(tmp_path: Path) -> None:
    store = SessionFile(tmp_path / "session.json")
    first = value()
    second = value()
    store.write(first)
    assert store.read() == first
    store.write(second)
    assert store.read() == second
    assert store.path.stat().st_mode & 0o777 == 0o600
    store.remove()
    assert not store.path.exists()
    assert not list(tmp_path.glob(".markos-session-*"))


def test_symlinked_parent_cannot_hide_repository_destination(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    nested = repo / "private"
    nested.mkdir(mode=0o700)
    link = tmp_path / "alias"
    link.symlink_to(nested, target_is_directory=True)
    with pytest.raises(ClientError):
        SessionFile(link / "session.json")


def test_client_document_limit_and_redacted_command_errors(capsys: Any) -> None:
    with pytest.raises(ClientError):
        document(b"x" * 262145)
    assert main(["--unrecognized-secret", "DO_NOT_ECHO"]) == 1
    captured = capsys.readouterr()
    assert "DO_NOT_ECHO" not in captured.err + captured.out

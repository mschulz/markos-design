import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path


def test_runtime_dependency_set_excludes_archive() -> None:
    requirements = importlib.metadata.requires("markos-runtime") or []
    assert any(r.startswith("pydantic==") for r in requirements)
    forbidden = {"markos", "duckdb", "fastapi", "markdown-it-py"}
    installed = {
        d.metadata["Name"].lower().replace("_", "-") for d in importlib.metadata.distributions()
    }
    assert not installed & forbidden


def test_guarded_preflight_outside_repository(config_file: Path, tmp_path: Path) -> None:
    # This process's environment contains only the independently installed package.
    # Guard actual opens/imports/socket construction, not just assertions on settings.
    program = r"""
import builtins, importlib.abc, os, pathlib, socket, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'markos', 'duckdb', 'fastapi', 'markdown_it'}:
            raise AssertionError('archive import attempted')
sys.meta_path.insert(0, Guard())
def deny(*args, **kwargs):
    raise AssertionError('network attempted')
socket.socket = deny
socket.create_connection = deny
socket.getaddrinfo = deny
original_open = pathlib.Path.open
def guarded_open(path, *args, **kwargs):
    if 'forbidden' in str(path):
        raise AssertionError('legacy document opened')
    return original_open(path, *args, **kwargs)
pathlib.Path.open = guarded_open
from markos_runtime.cli import main
assert main(['preflight', '--config', sys.argv[1]]) == 0
assert 'markos' not in sys.modules and 'duckdb' not in sys.modules
"""
    environment = dict(os.environ)
    for name in ["MARKOS_CONFIG", "MARKOS_VAULT_PATH", "MARKOS_MARKDOWN_ROOT", "MARKOS_INDEX_PATH"]:
        environment[name] = str(tmp_path / "forbidden")
    environment["MARKOS_RUNTIME_DATABASE_DSN"] = "NEVER_RESOLVE_OR_PRINT"
    environment["MARKOS_RUNTIME_BROKER_DSN"] = "NEVER_RESOLVE_OR_PRINT"
    result = subprocess.run(
        [sys.executable, "-I", "-c", program, str(config_file)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "NEVER_RESOLVE" not in result.stdout + result.stderr


def test_no_implicit_config_even_with_legacy_env(tmp_path: Path) -> None:
    environment = {**os.environ, "MARKOS_RUNTIME_CONFIG": "", "MARKOS_CONFIG": "forbidden"}
    result = subprocess.run(
        [sys.executable, "-I", "-m", "markos_runtime", "preflight"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "forbidden" not in result.stderr


def test_no_service_execution_command(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-m", "markos_runtime", "run"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_schemas_and_examples_match() -> None:
    from markos_runtime.config import load_config
    from markos_runtime.contracts import EventEnvelope
    from markos_runtime.schemas import artifacts, check

    repository = Path(__file__).resolve().parents[3]
    examples = repository / "config" / "runtime"
    assert check(examples / "schemas")
    config, manifest = load_config(examples / "runtime.example.toml", {})
    event = EventEnvelope.from_wire((examples / "noop.example.json").read_bytes())
    manifest.authorize_publish(event, config.service_id)
    from markos_runtime.contracts.schedules import ScheduleDefinition

    ScheduleDefinition.model_validate_json((examples / "schedule.example.json").read_bytes())
    assert len(artifacts()) == 41
    schema = json.loads(artifacts()["event.schema.json"])
    assert schema["additionalProperties"] is False
    assert "causation_id" in schema["required"]
    assert schema["x-max-wire-bytes"] == 65536


def test_schema_drift_detects_changed_and_extra_files(tmp_path: Path) -> None:
    from markos_runtime.schemas import artifacts, check

    for name, body in artifacts().items():
        (tmp_path / name).write_text(body)
    assert check(tmp_path)
    (tmp_path / "extra.schema.json").write_text("{}")
    assert not check(tmp_path)
    (tmp_path / "extra.schema.json").unlink()
    (tmp_path / "event.schema.json").write_text("{}")
    assert not check(tmp_path)


def test_schemas_are_valid_and_validate_examples() -> None:
    from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

    from markos_runtime.config import load_config
    from markos_runtime.contracts import EventEnvelope
    from markos_runtime.schemas import artifacts

    directory = Path(__file__).resolve().parents[3] / "config" / "runtime"
    config, _ = load_config(directory / "runtime.example.toml", {})
    event = EventEnvelope.from_wire((directory / "noop.example.json").read_bytes())
    data = {
        "replay.schema.json": json.loads((directory / "replay.example.json").read_text()),
        "schedule.schema.json": json.loads((directory / "schedule.example.json").read_text()),
        "event.schema.json": json.loads(event.to_wire()),
        "manifest.schema.json": json.loads((directory / "subscriptions.example.json").read_text()),
        "runtime-config.schema.json": config.model_dump(mode="json"),
    }
    for name, serialized in artifacts().items():
        schema = json.loads(serialized)
        Draft202012Validator.check_schema(schema)
        if name in data:
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(data[name])
    validator = Draft202012Validator(json.loads(artifacts()["event.schema.json"]))
    invalid = json.loads(event.to_wire())
    invalid["schema_version"] = True
    assert not validator.is_valid(invalid)
    invalid = json.loads(event.to_wire())
    invalid["payload_ref"] = {
        "source_id": "55555555-5555-4555-8555-555555555555",
        "owner_id": str(event.owner_id),
        "domain": event.domain,
        "sha256": "a" * 64,
    }
    assert not validator.is_valid(invalid)
    invalid["payload"] = None
    assert validator.is_valid(invalid)

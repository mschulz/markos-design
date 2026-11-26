import importlib.metadata
import json
import re
import subprocess
import sys
from pathlib import Path


def test_base_dependencies_exclude_optional_extras() -> None:
    # Optional extras carry an `extra ==` marker; everything else is installed by default.
    requirements = importlib.metadata.requires("markos-runtime") or []
    base = {re.split(r"[\[=; ]", r)[0] for r in requirements if "extra ==" not in r}
    assert base == {"aio-pika", "psycopg", "psycopg-pool", "pydantic"}


def test_no_service_execution_command(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-m", "markos_runtime", "run"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_schemas_and_examples_match() -> None:
    from markos_runtime.contracts import EventEnvelope
    from markos_runtime.contracts.policy import Manifest
    from markos_runtime.schemas import artifacts, check

    repository = Path(__file__).resolve().parents[1]
    examples = repository / "config" / "runtime"
    assert check(examples / "schemas")
    manifest = Manifest.from_wire((examples / "subscriptions.example.json").read_bytes())
    event = EventEnvelope.from_wire((examples / "noop.example.json").read_bytes())
    manifest.authorize_publish(event, "foundation-generator")
    from markos_runtime.contracts.schedules import ScheduleDefinition

    ScheduleDefinition.model_validate_json((examples / "schedule.example.json").read_bytes())
    assert len(artifacts()) == 43
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

    from markos_runtime.contracts import EventEnvelope
    from markos_runtime.schemas import artifacts

    directory = Path(__file__).resolve().parents[1] / "config" / "runtime"
    event = EventEnvelope.from_wire((directory / "noop.example.json").read_bytes())
    data = {
        "replay.schema.json": json.loads((directory / "replay.example.json").read_text()),
        "schedule.schema.json": json.loads((directory / "schedule.example.json").read_text()),
        "event.schema.json": json.loads(event.to_wire()),
        "manifest.schema.json": json.loads((directory / "subscriptions.example.json").read_text()),
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

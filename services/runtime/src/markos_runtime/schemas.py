"""Generate/check schemas only at an explicitly supplied output directory."""

import argparse
import json
from pathlib import Path

from markos_runtime.config import RuntimeConfig
from markos_runtime.contracts.base import ValueModel
from markos_runtime.contracts.events import EventEnvelope, TaskRequest
from markos_runtime.contracts.policy import Manifest
from markos_runtime.contracts.review import ReviewReference


def artifacts() -> dict[str, str]:
    models: dict[str, type[ValueModel]] = {
        "review": ReviewReference,
        "event": EventEnvelope,
        "task": TaskRequest,
        "manifest": Manifest,
        "runtime-config": RuntimeConfig,
    }
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
        for name, model in models.items()
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

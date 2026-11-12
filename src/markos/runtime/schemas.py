"""Explicit schema export/check command; never runs during package import."""

import argparse
import json
from pathlib import Path

from markos.runtime.contracts import ContractModel, EventEnvelope
from markos.runtime.settings import RuntimeSettings
from markos.runtime.topology import TopologyManifest


def schema_documents() -> dict[str, str]:
    models: dict[str, type[ContractModel]] = {
        "runtime.event-v1.schema.json": EventEnvelope,
        "runtime.settings.schema.json": RuntimeSettings,
        "runtime.topology-v1.schema.json": TopologyManifest,
    }
    return {
        name: json.dumps(
            {"$schema": "https://json-schema.org/draft/2020-12/schema",
             **model.model_json_schema()}, indent=2, sort_keys=True
        ) + "\n"
        for name, model in models.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    parser.add_argument("--directory", type=Path, default=Path("config"))
    args = parser.parse_args()
    for name, body in schema_documents().items():
        path = args.directory / name
        if args.write:
            path.write_text(body, encoding="utf-8")
        elif not path.is_file() or path.read_text(encoding="utf-8") != body:
            print(f"schema drift: {name}")
            return 1
    print("runtime schemas valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

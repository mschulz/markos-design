"""Validation-only command; no adapters, service loop, socket or secret resolution."""

import argparse
import sys
from pathlib import Path

from markos_runtime.config import load_config, read_bounded
from markos_runtime.contracts.base import MAX_EVENT_BYTES, ContractError
from markos_runtime.contracts.events import EventEnvelope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="markos-runtime")
    subcommands = parser.add_subparsers(dest="command", required=True)
    preflight = subcommands.add_parser(
        "preflight", help="Validate synthetic runtime documents only"
    )
    preflight.add_argument("--config", type=Path)
    preflight.add_argument("--event", type=Path)
    args = parser.parse_args(argv)
    try:
        config, manifest = load_config(args.config)
        if args.event:
            event = EventEnvelope.from_wire(read_bounded(args.event, MAX_EVENT_BYTES))
            if len(event.to_wire()) > config.limits.max_message_bytes:
                raise ContractError("event exceeds configured bound")
            if config.role != "publisher":
                raise ContractError("event preflight requires publisher role")
            manifest.authorize_publish(event, config.service_id)
    except ContractError:
        # Do not print chained parser exceptions, filenames, event bodies or settings.
        print(
            "Runtime preflight failed: invalid or missing explicit configuration/event.",
            file=sys.stderr,
        )
        return 2
    print("Runtime preflight passed (validation only; no connections or work executed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

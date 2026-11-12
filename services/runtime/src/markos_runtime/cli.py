"""Connection-free preflight and separately confirmed synthetic service commands."""

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="markos-runtime")
    subcommands = parser.add_subparsers(dest="command", required=True)
    preflight = subcommands.add_parser(
        "preflight", help="Validate synthetic runtime documents only"
    )
    preflight.add_argument("--config", type=Path)
    preflight.add_argument("--event", type=Path)
    service = subcommands.add_parser(
        "serve-synthetic", help="Run one explicitly configured test-only service"
    )
    service.add_argument("--config", type=Path, required=True)
    service.add_argument("--confirm-synthetic", action="store_true")
    health = subcommands.add_parser(
        "health", help="Check private local synthetic service readiness"
    )
    health.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    if args.command == "health":
        from urllib.request import urlopen

        if not 1024 <= args.port <= 65535:
            return 2
        try:
            with urlopen(f"http://127.0.0.1:{args.port}/ready", timeout=1) as response:
                return 0 if response.status == 200 else 1
        except OSError:
            return 1
    from markos_runtime.config import load_config, read_bounded
    from markos_runtime.contracts.base import MAX_EVENT_BYTES, ContractError
    from markos_runtime.contracts.events import EventEnvelope

    if args.command == "serve-synthetic":
        if not args.confirm_synthetic:
            print("Explicit synthetic confirmation required.", file=sys.stderr)
            return 2
        import os

        from markos_runtime.lifecycle.config import load_service
        from markos_runtime.lifecycle.service import run_service

        try:
            service_config, service_manifest = load_service(args.config)
        except ContractError:
            print("Invalid explicit synthetic service configuration.", file=sys.stderr)
            return 2
        return run_service(service_config, service_manifest, dict(os.environ))
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

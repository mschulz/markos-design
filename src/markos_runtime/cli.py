"""Connection-free control preflight and separately confirmed service commands."""

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="markos-runtime")
    subcommands = parser.add_subparsers(dest="command", required=True)
    control = subcommands.add_parser(
        "preflight-control", help="Validate control config and chat events only"
    )
    control.add_argument("--config", type=Path, required=True)
    control.add_argument("--event", type=Path)
    service = subcommands.add_parser(
        "serve-synthetic", help="Run one explicitly configured test-only service"
    )
    service.add_argument("--config", type=Path, required=True)
    service.add_argument("--confirm-synthetic", action="store_true")
    process = subcommands.add_parser(
        "serve-control-synthetic", help="Run an explicitly configured isolated control fixture"
    )
    process.add_argument("--config", type=Path, required=True)
    process.add_argument("--confirm-synthetic", action="store_true")
    production = subcommands.add_parser(
        "serve-control-production", help="Run one service under the production profile"
    )
    production.add_argument("--config", type=Path, required=True)
    production.add_argument("--confirm-production", action="store_true")
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
    from markos_runtime.config import read_bounded
    from markos_runtime.contracts.base import MAX_EVENT_BYTES, ContractError

    if args.command == "preflight-control":
        from markos_runtime.contracts.control import ChatEvent
        from markos_runtime.control.config import load_control

        try:
            load_control(args.config)
            if args.event:
                ChatEvent.from_wire(read_bounded(args.event, MAX_EVENT_BYTES))
        except ContractError:
            print("Invalid explicit control documents.", file=sys.stderr)
            return 2
        print("Control preflight passed (validation only; no authentication or connections).")
        return 0
    if args.command == "serve-control-synthetic":
        if not args.confirm_synthetic:
            print("Explicit synthetic confirmation required.", file=sys.stderr)
            return 2
        import os

        from markos_runtime.control_lifecycle.config import load_process

        try:
            process_config = load_process(args.config)
            from markos_runtime.control_lifecycle.service import run_control
        except (ContractError, ImportError):
            print("Invalid isolated control profile or optional installation.", file=sys.stderr)
            return 2
        return run_control(process_config, dict(os.environ))
    if args.command == "serve-control-production":
        if not args.confirm_production:
            print("Explicit production confirmation required.", file=sys.stderr)
            return 2
        from markos_runtime.control_lifecycle.production import FileSecrets, load_production

        try:
            production_config = load_production(args.config)
            from markos_runtime.control_lifecycle.service import run_control
        except (ContractError, ImportError):
            print("Invalid production profile or optional installation.", file=sys.stderr)
            return 2
        # Secrets come only from the mounted files, never from the environment.
        return run_control(production_config, FileSecrets(production_config.secrets_dir))
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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

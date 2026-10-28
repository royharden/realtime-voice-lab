"""Explicit local entrypoints; importing this module starts no services."""

from __future__ import annotations

import argparse
import json
import os
from importlib import import_module
from pathlib import Path
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int | None:
    parser = argparse.ArgumentParser(description="Realtime Voice Lab (keyless mock by default)")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the local mock relay after runtime authorization")
    serve.add_argument("--mode", choices=["mock"], default="mock")
    serve.add_argument("--host", choices=["127.0.0.1"], default="127.0.0.1")
    serve.add_argument("--port", type=int, required=True, help="A port already claimed via agent-ports")
    serve.add_argument("--port-claim", required=True, type=Path, help="Existing port claim receipt")
    serve.add_argument("--run-id", default="mock-local", help="Frozen run identity, preserved in evidence")
    serve.add_argument("--run-manifest-hash", help="Operator-supplied SHA-256 link; not execution proof")
    serve.add_argument("--artifact-root", type=Path, default=Path(".agent-work/runtime"))
    serve.add_argument("--allowed-origin", help="Exact registered loopback browser origin")
    serve.add_argument("--mock-fault-policy", type=Path, help="Explicit operator mock-only policy JSON")
    delegates = {
        "trial-plan": ("voicelab.evaluation.cli", "plan"),
        "run": ("voicelab.evaluation.cli", "run"),
        "eval": ("voicelab.evaluation.cli", "report"),
        "assemble": ("voicelab.evaluation.cli", "assemble"),
        "review-packet": ("voicelab.evaluation.cli", "review-packet"),
        "review-append": ("voicelab.evaluation.cli", "review-append"),
        "review-export": ("voicelab.evaluation.cli", "review-export"),
        "custody-import": ("voicelab.evaluation.cli", "custody-import"),
        "reproduce": ("voicelab.evaluation.cli", "reproduce"),
        "calibrate": ("voicelab.harness.cli", "calibrate"),
        "soak": ("voicelab.harness.cli", "soak"),
        "audit-split": ("voicelab.harness.cli", "audit-split"),
        "demo": ("voicelab.harness.cli", "demo"),
        "cohort": ("voicelab.harness.cli", "cohort"),
        "import-fixture-catalog": ("voicelab.harness.cli", "import-fixture-catalog"),
        "generate-dev-fixture": ("voicelab.harness.cli", "generate-dev-fixture"),
    }
    for command in delegates:
        commands.add_parser(command, add_help=False, help=f"Delegate to the {command} source component")
    args, remaining = parser.parse_known_args(argv)
    if args.command in delegates:
        module_name, subcommand = delegates[args.command]
        try:
            component = import_module(module_name)
        except ModuleNotFoundError as error:
            if error.name and (module_name == error.name or module_name.startswith(error.name + ".")):
                parser.error(f"Source component {module_name} is unavailable in this checkout")
            raise
        return component.main([subcommand, *remaining])
    if remaining:
        parser.error("Unrecognized serve arguments; see serve --help")
    if args.command == "serve":
        if not 20000 <= args.port <= 29999:
            parser.error("Port must be in the machine-wide reserved 20000–29999 band")
        if not args.port_claim.is_file():
            parser.error("A real agent-ports claim receipt must exist before listener activation")
        # A receipt path is an operator checkpoint, not independent ownership verification.
        # The agent-ports check remains required immediately before actual later activation.
        import uvicorn
        from voicelab.server.app import Settings, create_app

        fault_policy = None
        if args.mock_fault_policy is not None:
            try:
                if args.mock_fault_policy.stat().st_size > 16_384:
                    parser.error("Mock fault policy exceeds the configuration bound")
                fault_policy = json.loads(args.mock_fault_policy.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                parser.error("Mock fault policy must be a readable UTF-8 JSON object")
            if not isinstance(fault_policy, dict):
                parser.error("Mock fault policy must be a JSON object")
        settings = Settings(
            nonce=os.environ.get("VOICELAB_NONCE", os.environ.get("VOICELAB_SESSION_NONCE", "")),
            allowed_origin=args.allowed_origin or os.environ.get("VOICELAB_ALLOWED_ORIGIN", ""),
            artifact_root=args.artifact_root,
            mode=args.mode,
            run_id=args.run_id,
            run_manifest_hash=args.run_manifest_hash,
            mock_fault_policy=fault_policy,
        )
        from voicelab.runtime_logging import listener_logging_config

        uvicorn.run(
            create_app(settings), host=args.host, port=args.port,
            ws="websockets", ws_max_size=24_000,
            access_log=False, log_level="warning",
            log_config=listener_logging_config(),
        )


if __name__ == "__main__":
    raise SystemExit(main())

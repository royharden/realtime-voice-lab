"""Explicit local entrypoints; importing this module starts no services."""

from __future__ import annotations

import argparse
from importlib import import_module
from pathlib import Path
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Realtime Voice Lab (keyless mock by default)")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the local mock relay after runtime authorization")
    serve.add_argument("--mode", choices=["mock"], default="mock")
    serve.add_argument("--host", choices=["127.0.0.1"], default="127.0.0.1")
    serve.add_argument("--port", type=int, required=True, help="A port already claimed via agent-ports")
    serve.add_argument("--port-claim", required=True, type=Path, help="Existing port claim receipt")
    delegates = {
        "run": ("voicelab.evaluation.cli", "run"),
        "eval": ("voicelab.evaluation.cli", "report"),
        "calibrate": ("voicelab.harness.cli", "calibrate"),
        "soak": ("voicelab.harness.cli", "soak"),
        "audit-split": ("voicelab.harness.cli", "audit-split"),
        "demo": ("voicelab.harness.cli", "demo"),
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
        component.main([subcommand, *remaining])
        return
    if remaining:
        parser.error("Unrecognized serve arguments: " + " ".join(remaining))
    if args.command == "serve":
        if not 20000 <= args.port <= 29999:
            parser.error("Port must be in the machine-wide reserved 20000–29999 band")
        if not args.port_claim.is_file():
            parser.error("A real agent-ports claim receipt must exist before listener activation")
        # A receipt path is an operator checkpoint, not independent ownership verification.
        # The agent-ports check remains required immediately before actual later activation.
        import uvicorn

        uvicorn.run("voicelab.server.app:create_app", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

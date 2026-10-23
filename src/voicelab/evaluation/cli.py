"""Offline artifact operations; this entry point never launches the voice driver."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .artifacts import canonical_bytes
from .assembly import assemble_evaluation, write_evaluation
from .planning import create_trial_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="create a frozen prospective 40+8 paired schedule; execute nothing")
    plan.add_argument("--request", required=True)
    plan.add_argument("--out", required=True)
    assemble = sub.add_parser("assemble", help="assemble saved dev evidence and invoke the canonical reporter")
    assemble.add_argument("--bundle", required=True)
    assemble.add_argument("--artifact-root", required=True)
    assemble.add_argument("--freeze-hash", help="externally supplied expected canonical freeze hash")
    assemble.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.command == "plan":
        result = create_trial_plan(json.loads(Path(args.request).read_text(encoding="utf-8")))
        # Exclusive creation: changing a published plan requires a new file and cohort.
        with Path(args.out).open("xb") as stream:
            stream.write(canonical_bytes(result))
    else:
        assembled = assemble_evaluation(json.loads(Path(args.bundle).read_text(encoding="utf-8")),
                                        args.artifact_root, expected_freeze_hash=args.freeze_hash)
        # No input file can load code or install a verifier. CLI defaults fail closed for heldout.
        write_evaluation(assembled, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

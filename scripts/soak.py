"""Analyze captured soak series; does not run the separately deferred browser soak."""
import argparse
import json
from pathlib import Path

from voicelab.reporting.diagnostics import soak_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observations', required=True, help='JSON {samples:[...],summary:{...}}')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.observations).read_text(encoding='utf-8'))
    report = soak_report(source['samples'], source['summary'])
    Path(args.out).write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

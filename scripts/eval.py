"""Future use: python scripts/eval.py --manifest RUN --rows TURNS --ledger LEDGER --out DIR.

Requires installed local package. Consumes approved synthetic artifacts, never calls providers.
"""
import argparse

from voicelab.reporting import write_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'rows', 'ledger', 'out'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    write_report(args.manifest, args.rows, args.ledger, args.out)


if __name__ == '__main__':
    main()

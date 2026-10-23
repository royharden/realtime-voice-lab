"""Future use: python scripts/eval.py --manifest RUN --rows TURNS --ledger LEDGER --out DIR.

Requires installed local package. Consumes approved synthetic artifacts, never calls providers.
"""
import sys

from voicelab.evaluation.cli import main as evaluation_main


def main():
    return evaluation_main(['report', *sys.argv[1:]])


if __name__ == '__main__':
    main()

"""Structural comparison only; cannot release independent heldout custody."""
import sys
from voicelab.harness.cli import main

if __name__ == '__main__':
    main(['audit-split', *sys.argv[1:]])

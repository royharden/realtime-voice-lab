"""Operator-activated full/short soak source; no runtime on import."""
import sys
from voicelab.harness.cli import main

if __name__ == '__main__':
    arguments = sys.argv[1:]
    local = '--local-synthetic' in arguments
    raise SystemExit(main(['local-synthetic-soak' if local else 'soak',
                          *[value for value in arguments if value != '--local-synthetic']]))

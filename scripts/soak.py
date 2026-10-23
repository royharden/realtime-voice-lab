"""Operator-activated full/short soak source; no runtime on import."""
import sys
from voicelab.harness.cli import main

if __name__ == '__main__':
    main(['soak', *sys.argv[1:]])

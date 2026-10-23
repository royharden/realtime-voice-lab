"""Operator-activated calibration source; no runtime on import."""
import sys
from voicelab.harness.cli import main

if __name__ == '__main__':
    main(['calibrate', *sys.argv[1:]])

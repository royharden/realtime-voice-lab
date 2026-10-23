"""Print the fixed development walkthrough; no video or runtime implied."""
import sys
from voicelab.harness.cli import main

if __name__ == '__main__':
    main(['demo', *sys.argv[1:]])

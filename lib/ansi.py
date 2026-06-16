"""ANSI color constants for Python scripts — matches lib/output.sh."""
import os
import sys

if os.environ.get('NO_COLOR') or not sys.stderr.isatty():
    BOLD = GREEN = BLUE = YELLOW = RED = CYAN = DIM = NC = ''
else:
    BOLD = '\033[1m'
    GREEN = '\033[0;32m'
    BLUE = '\033[0;34m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    CYAN = '\033[0;36m'
    DIM = '\033[2m'
    NC = '\033[0m'

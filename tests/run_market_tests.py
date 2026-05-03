"""Deprecated: use pytest instead.

    pytest tests/ -m market

This file is kept to avoid breaking any existing bookmarks or scripts,
but is no longer the test runner. See tests/test_market_hours.py.
"""

import subprocess
import sys

if __name__ == "__main__":
    sys.exit(subprocess.call(["pytest", "tests/", "-m", "market"] + sys.argv[1:]))

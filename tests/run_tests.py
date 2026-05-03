"""Deprecated: use pytest instead.

    pytest tests/ -m "not market"

This file is kept to avoid breaking any existing bookmarks or scripts,
but is no longer the test runner. See tests/conftest.py and tests/test_*.py.
"""

import subprocess
import sys

if __name__ == "__main__":
    sys.exit(subprocess.call(["pytest", "tests/", "-m", "not market"] + sys.argv[1:]))

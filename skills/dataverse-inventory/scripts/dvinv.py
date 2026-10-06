#!/usr/bin/env python3
"""Launcher: python3 scripts/dvinv.py <check|extract|render|all|diff|collectors> -c inventory.yaml"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dvinv.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

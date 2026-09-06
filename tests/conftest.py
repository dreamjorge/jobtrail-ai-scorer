"""Pytest configuration shared by every test module.

The hermetic end-to-end suite imports the in-process stubs under
``tests/stubs/``. That directory lives below the implicit ``tests/`` package
root that pytest already discovers, so we add ``tests/`` to ``sys.path`` here
once at collection time. Doing it in ``conftest.py`` (rather than at the top
of every test module) keeps the import path stable across the e2e module and
any future stub-based tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

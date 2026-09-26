"""Test-wide safety net: point config.DATABASE_PATH at a throwaway file
BEFORE anything imports `database` (whose module import runs init_db() as a
side effect). Without this, importing `database` for the first time during
test collection would run migrations against the real receipts.db.
"""

import tempfile
from pathlib import Path

import config

config.DATABASE_PATH = Path(tempfile.gettempdir()) / "receipt_manager_test_import_guard.db"

#!/usr/bin/env python3
"""
Creates an empty res_domus.db with the full schema and views, but no
purchases — for starting fresh with your own data (no demo rows).

Usage:
    python scripts/init_db.py
"""

import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from parser.build_db import SCHEMA, VIEWS  # noqa: E402

DATA_DIR = BASE_DIR.parent / "data"
DB_PATH = DATA_DIR / "res_domus.db"


def main():
    if DB_PATH.exists():
        print(f"{DB_PATH.name} already exists — leaving it untouched.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.executescript(VIEWS)
    conn.commit()
    conn.close()
    print(f"Created empty {DB_PATH.name}.")


if __name__ == "__main__":
    main()

"""
Shared pytest fixtures. Every test runs against an isolated DB under
data/test_runs/<random-name>/ via the app's own TEST_RUN mechanism (see
app/config.py) — tests must never read or write data/res_domus.db.
"""

import base64
import importlib
import shutil
import sys
import uuid
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

TEST_USER = "testuser"
TEST_PASS = "testpass"


@pytest.fixture
def flask_app(monkeypatch):
    run_name = f"pytest_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("TEST_RUN", run_name)
    monkeypatch.setenv("BASIC_AUTH_USER", TEST_USER)
    monkeypatch.setenv("BASIC_AUTH_PASS", TEST_PASS)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    # config.py reads env vars at import time; reload so the values above
    # take effect even if a previous test already imported/cached it.
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])

    from app import create_app
    application = create_app()
    application.config["TESTING"] = True

    yield application

    shutil.rmtree(Path(application.config["DB_PATH"]).parent, ignore_errors=True)


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture
def auth_headers():
    creds = base64.b64encode(f"{TEST_USER}:{TEST_PASS}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


@pytest.fixture
def seeded_db(flask_app):
    """Create the purchases table with one row, isolated under this test's TEST_RUN dir."""
    import sqlite3
    from parser.build_db import SCHEMA, INSERT_SQL

    conn = sqlite3.connect(flask_app.config["DB_PATH"])
    conn.executescript(SCHEMA)
    conn.execute(INSERT_SQL, {
        "raw_name": "Leche Gloria", "matched_id": "leche_gloria",
        "matched_category": "Abarrotes", "matched_subcategory": "Lacteos",
        "tags": None, "unit": "l", "quantity": 2, "unit_price": 4.5,
        "total_price": 9.0, "source": "Tottus", "order_id": "t-0001",
        "payment_method": "Tarjeta", "datetime": "2026-07-01",
        "gpt_notes": None, "source_file": "pytest-seed",
    })
    conn.commit()
    conn.close()
    return flask_app.config["DB_PATH"]

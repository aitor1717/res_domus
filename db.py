"""
Database connection helper.

Local dev:  plain sqlite3 (fast, no credentials needed)
Production: libsql (Turso) when TURSO_URL is set in config

Usage in blueprints:
    from db import get_conn, rows_as_dicts
    conn = get_conn()
    cur = conn.execute("SELECT ...")
    rows = rows_as_dicts(cur)
    conn.close()
"""

import sqlite3
import os
from flask import current_app


def get_conn():
    turso_url = current_app.config.get("TURSO_URL", "")
    turso_token = current_app.config.get("TURSO_TOKEN", "")

    if turso_url:
        import libsql_client
        return libsql_client.create_client_sync(url=turso_url, auth_token=turso_token)

    return sqlite3.connect(current_app.config["DB_PATH"])


def rows_as_dicts(result) -> list[dict]:
    """Works with both sqlite3.Cursor and libsql_client ResultSet."""
    if hasattr(result, "description"):
        # sqlite3 cursor
        cols = [d[0] for d in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]
    # libsql ResultSet
    cols = [c.name for c in result.columns]
    return [dict(zip(cols, row)) for row in result.rows]

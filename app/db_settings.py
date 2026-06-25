"""
Small key/value store in res_domus.db for runtime-configurable settings
(e.g. an Anthropic API key entered via the Settings page), so AI features
can be turned on without editing config.py or restarting the container.
"""

import sqlite3

_SCHEMA = "CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)"


def get_setting(db_path: str, key: str) -> str | None:
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else None


def set_setting(db_path: str, key: str, value: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_anthropic_key(db_path: str, config_key: str) -> str:
    """Settings-page override takes priority over config.py/env."""
    return get_setting(db_path, "anthropic_api_key") or config_key

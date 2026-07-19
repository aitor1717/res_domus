"""
Unit tests for api/chat.py's _sql_scope_ok() — the table/view allow-list that
guards AI-generated SQL before it's executed against a read-only connection.
Pure function, no Flask app context needed.
"""

from api.chat import _sql_scope_ok


def test_allows_documented_view():
    assert _sql_scope_ok("SELECT * FROM v_monthly_spend WHERE month = '2026-07'")


def test_allows_join_across_two_allowed_tables():
    sql = "SELECT p.id FROM purchases p JOIN budget b ON b.month = strftime('%Y-%m', p.datetime)"
    assert _sql_scope_ok(sql)


def test_rejects_app_settings():
    assert not _sql_scope_ok("SELECT value FROM app_settings WHERE key = 'anthropic_api_key'")


def test_rejects_sqlite_internals():
    assert not _sql_scope_ok("SELECT name FROM sqlite_master")


def test_cte_alias_referencing_allowed_table_is_ok():
    sql = "WITH recent AS (SELECT * FROM v_price_history) SELECT * FROM recent"
    assert _sql_scope_ok(sql)


def test_cte_wrapped_disallowed_table_still_rejected():
    """Wrapping a disallowed table in a CTE must not bypass the allow-list."""
    sql = "WITH leak AS (SELECT * FROM app_settings) SELECT * FROM leak"
    assert not _sql_scope_ok(sql)


def test_rejects_query_with_no_table_reference():
    assert not _sql_scope_ok("SELECT 1")

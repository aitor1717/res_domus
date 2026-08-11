"""
Coverage for api/settings.py - previously untested. Covers budget overrides,
API-key status/storage (via db_settings, not config.py directly - see
CLAUDE.md's Config section), xlsx export, and the reimport endpoint's
no-op path. Anything requiring a real Anthropic call is out of scope.
"""

import sqlite3


def test_set_budget_requires_month(client, auth_headers, empty_db):
    resp = client.post("/api/settings/budget", json={"manual_budget": 500}, headers=auth_headers)
    assert resp.status_code == 400


def test_set_budget_upserts_and_reads_back(client, auth_headers, empty_db):
    resp = client.post(
        "/api/settings/budget",
        json={"month": "2026-07", "manual_budget": 500},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    conn = sqlite3.connect(empty_db)
    row = conn.execute("SELECT manual_budget FROM budget WHERE month = '2026-07'").fetchone()
    conn.close()
    assert row[0] == 500

    # upsert: posting again for the same month updates rather than duplicates
    client.post("/api/settings/budget", json={"month": "2026-07", "manual_budget": 650}, headers=auth_headers)
    conn = sqlite3.connect(empty_db)
    rows = conn.execute("SELECT manual_budget FROM budget WHERE month = '2026-07'").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == 650


def test_set_budget_null_deletes_override(client, auth_headers, empty_db):
    client.post("/api/settings/budget", json={"month": "2026-07", "manual_budget": 500}, headers=auth_headers)
    client.post("/api/settings/budget", json={"month": "2026-07", "manual_budget": None}, headers=auth_headers)

    conn = sqlite3.connect(empty_db)
    row = conn.execute("SELECT * FROM budget WHERE month = '2026-07'").fetchone()
    conn.close()
    assert row is None


def test_api_key_status_starts_unconfigured(client, auth_headers, empty_db):
    resp = client.get("/api/settings/api-key", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["configured"] is False
    assert body["source"] is None


def test_set_api_key_then_status_reports_configured(client, auth_headers, empty_db):
    resp = client.post("/api/settings/api-key", json={"api_key": "sk-ant-test123"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.get_json()["configured"] is True

    status = client.get("/api/settings/api-key", headers=auth_headers).get_json()
    assert status["configured"] is True
    assert status["source"] == "settings"


def test_export_xlsx_returns_spreadsheet(client, auth_headers, seeded_db):
    resp = client.get("/api/settings/export", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert resp.data[:2] == b"PK"  # xlsx is a zip archive


def test_export_xlsx_neutralizes_formula_injection(client, auth_headers, empty_db):
    """Regression for the 2026-08-11 audit finding: raw_name/source come from
    Claude's OCR read of a photographed receipt, not a trusted internal
    source. A value starting with =/+/-/@ must not reach the spreadsheet as
    a live formula - openpyxl auto-detects a leading "=" as a formula type
    only when the raw string starts with it, so this also checks the cell's
    data_type, not just its displayed text."""
    import io
    import openpyxl
    from conftest import insert_purchase

    conn = sqlite3.connect(empty_db)
    insert_purchase(
        conn, raw_name="=cmd|'/c calc'!A1", source="@SUM(1,1)",
        matched_id="formula_test", datetime="2026-01-05",
    )
    conn.commit()
    conn.close()

    resp = client.get("/api/settings/export", headers=auth_headers)
    assert resp.status_code == 200

    wb = openpyxl.load_workbook(io.BytesIO(resp.data))
    ws = wb.active
    row = next(r for r in ws.iter_rows(min_row=2) if r[1].value and "cmd" in str(r[1].value))
    raw_name_cell, source_cell = row[1], row[7]

    assert raw_name_cell.data_type != "f"  # not treated as a live formula
    assert raw_name_cell.value.startswith("'=")
    assert source_cell.value.startswith("'@")


def test_reimport_with_no_csvs_is_a_noop(client, auth_headers, empty_db):
    resp = client.post("/api/settings/reimport", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["inserted"] == 0
    assert body["files_processed"] == 0


def test_budget_requires_auth(client, empty_db):
    resp = client.post("/api/settings/budget", json={"month": "2026-07"})
    assert resp.status_code == 401


def test_writes_blocked_in_demo_mode(demo_client):
    resp = demo_client.post("/api/settings/budget", json={"month": "2026-07", "manual_budget": 500})
    assert resp.status_code == 403

"""
Regression test for the chart anomaly-index bug (2026-07-26 audit finding):
GET /api/chart matched an already-formatted display label (e.g. "mar 2026")
against a raw "YYYY-MM" bucket string and could never find the anomaly's
bucket, so anomalyIdx was always null and the anomaly marker never rendered
on the dashboard chart, in every period.

The fix keeps the raw ISO bucket dates alongside the formatted labels
(see api/dashboard.py's chart()) so the match is done on comparable values.
"""

import sqlite3

from parser.build_db import INSERT_SQL, SCHEMA, VIEWS

MATCHED_ID = "anomaly_test_item"


def _insert(conn, datetime_, unit_price, quantity=1):
    conn.execute(INSERT_SQL, {
        "raw_name": "Anomaly Test Item", "matched_id": MATCHED_ID,
        "matched_category": "Abarrotes", "matched_subcategory": None,
        "tags": None, "unit": "u", "quantity": quantity, "unit_price": unit_price,
        "total_price": round(unit_price * quantity, 2), "source": "Test Store",
        "order_id": "t-anom", "payment_method": "Tarjeta", "datetime": datetime_,
        "gpt_notes": None, "source_file": "pytest-anomaly-seed",
    })


def _seed_anomaly(db_path):
    """20 normal-priced purchases plus one extreme outlier in March, all in
    the past - the population z-score for the outlier exceeds 3 regardless
    of period, and no row falls in the current month, so the chart's
    trailing-incomplete-bucket-drop logic never touches this data."""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    for day in range(1, 21):
        _insert(conn, f"2026-01-{day:02d}", 2.0)
    _insert(conn, "2026-03-15", 1000.0)
    conn.commit()
    conn.executescript(VIEWS)
    conn.close()


def test_chart_all_period_finds_anomaly_bucket(client, auth_headers, flask_app):
    _seed_anomaly(flask_app.config["DB_PATH"])

    resp = client.get("/api/chart?period=all", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["anomalyIdx"] is not None
    assert 0 <= body["anomalyIdx"] < len(body["labels"])
    assert "mar" in body["labels"][body["anomalyIdx"]].lower()
    assert "2026" in body["anomalyLabel"]["en"]
    assert "anomaly" in body["anomalyLabel"]["en"]

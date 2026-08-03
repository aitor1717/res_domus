"""
v_item_stats (parser/build_db.py:52-113) — a view, not an endpoint, so it's
queried directly against the real DB (the methodology's "or real SQL view"
option) via a raw sqlite3 connection to the same DB the app itself would use.

Every expected value is hand-computed plain arithmetic from a purpose-built
dataset, never re-derived by re-running the view's own SQL. Dates are
relative to date.today() at test-run time.
"""

import sqlite3
from datetime import timedelta

from conftest import insert_purchase, utc_today


def _iso(days_ago: int) -> str:
    return (utc_today() - timedelta(days=days_ago)).isoformat()


def _stats_row(db_path, matched_id) -> dict | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM v_item_stats WHERE matched_id = ?", (matched_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def test_is_reliable_boundary_at_exactly_3_purchases(client, auth_headers, empty_db):
    conn = sqlite3.connect(empty_db)
    for n in (20, 10, 0):  # 3 purchases -> reliable
        insert_purchase(conn, matched_id="boundary_3", datetime=_iso(n), quantity=1,
                         unit_price=1.0, total_price=1.0)
    for n in (10, 0):  # 2 purchases -> not reliable
        insert_purchase(conn, matched_id="boundary_2", datetime=_iso(n), quantity=1,
                         unit_price=1.0, total_price=1.0)
    conn.commit()
    conn.close()

    row3 = _stats_row(empty_db, "boundary_3")
    row2 = _stats_row(empty_db, "boundary_2")
    assert row3["purchase_count"] == 3
    assert row3["is_reliable"] == 1
    assert row2["purchase_count"] == 2
    assert row2["is_reliable"] == 0


def test_interval_consumption_and_urgency_not_clamped(client, auth_headers, empty_db):
    """One dataset, all values chosen so every intermediate division is exact
    (no rounding ambiguity):

    3 purchases of "negative_stock_item" at 90, 80, 60 days ago:
      quantities 5, 5, 2 (last/most recent purchase = qty 2)
      gaps: 10 days, then 20 days -> avg_interval_days = (10+20)/2 = 15
      avg_quantity = (5+5+2)/3 = 4
      daily_consumption = avg_quantity / avg_interval_days = 4/15
      days_since_last = 60 (last purchase 60 days ago)
      est_stock_remaining = last_quantity - daily_consumption*days_since_last
                           = 2 - (4/15)*60 = 2 - 16 = -14   (negative -- you're
                           long overdue, and the view does NOT clamp this at 0)
      reorder_urgency = days_since_last / avg_interval_days = 60/15 = 4.0
                           (over 1.0 -- also not clamped)
    """
    conn = sqlite3.connect(empty_db)
    insert_purchase(conn, matched_id="negative_stock_item", datetime=_iso(90),
                     quantity=5, unit_price=1.0, total_price=5.0)
    insert_purchase(conn, matched_id="negative_stock_item", datetime=_iso(80),
                     quantity=5, unit_price=1.0, total_price=5.0)
    insert_purchase(conn, matched_id="negative_stock_item", datetime=_iso(60),
                     quantity=2, unit_price=1.0, total_price=2.0)
    conn.commit()
    conn.close()

    row = _stats_row(empty_db, "negative_stock_item")
    assert row["avg_interval_days"] == 15.0
    assert row["last_quantity"] == 2
    assert row["days_since_last"] == 60
    assert row["daily_consumption"] == round(4 / 15, 4)
    assert row["est_stock_remaining"] == -14.0
    assert row["est_stock_remaining"] < 0  # explicitly NOT clamped at 0
    assert row["reorder_urgency"] == 4.0
    assert row["reorder_urgency"] > 1.0  # explicitly NOT clamped at 1.0


def test_std_unit_price_population_stdev(client, auth_headers, empty_db):
    """Two purchases at 1.0 and 5.0: population mean=3.0, population variance
    = avg(x^2) - avg(x)^2 = (1+25)/2 - 9 = 13-9 = 4 -> std = sqrt(4) = 2.0
    exactly. (Population, not sample, stdev -- the view's SQL literally uses
    SQRT(AVG(x*x)-AVG(x)*AVG(x)), the population formula.)"""
    conn = sqlite3.connect(empty_db)
    insert_purchase(conn, matched_id="stdev_item", datetime=_iso(10),
                     quantity=1, unit_price=1.0, total_price=1.0)
    insert_purchase(conn, matched_id="stdev_item", datetime=_iso(5),
                     quantity=1, unit_price=5.0, total_price=5.0)
    conn.commit()
    conn.close()

    row = _stats_row(empty_db, "stdev_item")
    assert row["avg_unit_price"] == 3.0
    assert row["std_unit_price"] == 2.0


def test_single_purchase_item_has_zero_stdev_and_never_flagged_anomalous(client, auth_headers, empty_db):
    """A single purchase can't deviate from its own average: std_unit_price
    must be 0 (not NULL, not a crash), and v_anomalies' `std_unit_price > 0`
    guard must keep this item out of the anomalies view entirely (it can
    never produce a real z-score off just one data point)."""
    conn = sqlite3.connect(empty_db)
    insert_purchase(conn, matched_id="single_purchase_item", datetime=_iso(3),
                     quantity=1, unit_price=10.0, total_price=10.0)
    conn.commit()
    conn.close()

    row = _stats_row(empty_db, "single_purchase_item")
    assert row["purchase_count"] == 1
    assert row["std_unit_price"] == 0.0

    conn = sqlite3.connect(empty_db)
    anomaly_count = conn.execute(
        "SELECT COUNT(*) FROM v_anomalies WHERE matched_id = ?", ("single_purchase_item",)
    ).fetchone()[0]
    conn.close()
    assert anomaly_count == 0

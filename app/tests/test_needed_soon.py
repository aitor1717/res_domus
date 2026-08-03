"""
GET /api/needed-soon (api/dashboard.py:268-324) — the most structurally
complex query in the module: stratified bucket selection over v_item_stats,
plus urgency_pct/urgency_color/days_left post-processing done in Python.

Every expected value is hand-computed plain arithmetic from a purpose-built
dataset, never re-derived by re-running the endpoint's own SQL/logic. Dates
are relative to utc_today() (see conftest.utc_today) since days_since_last
is computed via julianday('now'), which is UTC-based.
"""

import sqlite3
from datetime import timedelta

from conftest import insert_purchase, utc_today

DELIVERY_CATEGORY = "Delivery"


def _iso(days_ago: int):
    return (utc_today() - timedelta(days=days_ago)).isoformat()


def _seed_reliable_item(conn, matched_id, urgency, category="Pantry"):
    """3 purchases spaced exactly 100 days apart -> avg_interval_days = 100
    exactly, so reorder_urgency = days_since_last / 100 = the requested
    urgency exactly (days_since_last chosen as round(urgency*100), an
    integer number of days)."""
    days_since_last = round(urgency * 100)
    for offset in (days_since_last + 200, days_since_last + 100, days_since_last):
        insert_purchase(conn, matched_id=matched_id, matched_category=category,
                         raw_name=matched_id, datetime=_iso(offset),
                         quantity=1, unit_price=1.0, total_price=1.0)


def _items_by_id(body):
    return {row["matched_id"]: row for row in body["items"]}


def test_urgency_pct_caps_at_100(client, auth_headers, empty_db):
    """reorder_urgency = 12.0 (way overdue) -> urgency_pct = min(100, round(1200)) = 100."""
    conn = sqlite3.connect(empty_db)
    _seed_reliable_item(conn, "critical_capped", urgency=12.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/needed-soon", headers=auth_headers)
    assert resp.status_code == 200
    row = _items_by_id(resp.get_json())["critical_capped"]
    assert row["reorder_urgency"] > 1.0
    assert row["urgency_pct"] == 100


def test_urgency_color_boundaries(client, auth_headers, empty_db):
    """Exact reorder_urgency values chosen so urgency_pct lands on exactly
    79, 80, 60, 59, 40, 39 -- the boundaries of _urgency_color's four tiers
    (>=80 critical, >=60 low, >=40 watch, else fine)."""
    targets = {
        "u79": (0.79, "#FF9D6E"),   # < 80 -> falls to the >=60 tier
        "u80": (0.80, "#FF6F91"),   # == 80 -> critical
        "u60": (0.60, "#FF9D6E"),   # == 60 -> low
        "u59": (0.59, "#FFE0A3"),   # < 60 -> falls to the >=40 tier
        "u40": (0.40, "#FFE0A3"),   # == 40 -> watch
        "u39": (0.39, "rgba(100,200,140,.8)"),  # < 40 -> fine
    }
    conn = sqlite3.connect(empty_db)
    for matched_id, (urgency, _color) in targets.items():
        _seed_reliable_item(conn, matched_id, urgency)
    conn.commit()
    conn.close()

    resp = client.get("/api/needed-soon", headers=auth_headers)
    assert resp.status_code == 200
    by_id = _items_by_id(resp.get_json())

    expected_pct = {"u79": 79, "u80": 80, "u60": 60, "u59": 59, "u40": 40, "u39": 39}
    for matched_id, (urgency, expected_color) in targets.items():
        row = by_id[matched_id]
        assert row["urgency_pct"] == expected_pct[matched_id], matched_id
        assert row["urgency_color"] == expected_color, matched_id


def test_days_left_stock_based_formula(client, auth_headers, empty_db):
    """daily_consumption > 0: days_left = round(est_stock_remaining / daily_consumption).
    3 purchases of qty 10 each, spaced 10 days apart (avg_interval=10),
    last purchase 4 days ago:
      daily_consumption = avg_quantity/avg_interval = 10/10 = 1.0
      est_stock_remaining = last_quantity - daily_consumption*days_since_last
                           = 10 - 1*4 = 6
      days_left = round(6/1) = 6
    """
    conn = sqlite3.connect(empty_db)
    for offset in (24, 14, 4):
        insert_purchase(conn, matched_id="days_left_stock_based", matched_category="Pantry",
                         raw_name="x", datetime=_iso(offset), quantity=10,
                         unit_price=1.0, total_price=10.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/needed-soon", headers=auth_headers)
    row = _items_by_id(resp.get_json())["days_left_stock_based"]
    assert row["daily_consumption"] == 1.0
    assert row["est_stock_remaining"] == 6.0
    assert row["days_left"] == 6


def test_days_left_fallback_formula_when_daily_consumption_is_zero(client, auth_headers, empty_db):
    """daily_consumption == 0 (all quantities 0, an edge case reachable only
    via a direct/raw insert like this test does -- the app's normal write
    paths always recompute unit_price from a nonzero quantity, but the view
    itself places no such constraint) falls back to
    days_left = round(avg_interval_days - days_since_last).
    3 purchases of qty 0, spaced 10 days apart (avg_interval=10), last
    purchase 3 days ago: days_left = round(10 - 3) = 7.
    """
    conn = sqlite3.connect(empty_db)
    for offset in (23, 13, 3):
        insert_purchase(conn, matched_id="days_left_fallback", matched_category="Pantry",
                         raw_name="x", datetime=_iso(offset), quantity=0,
                         unit_price=0.0, total_price=0.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/needed-soon", headers=auth_headers)
    row = _items_by_id(resp.get_json())["days_left_fallback"]
    assert row["daily_consumption"] == 0.0
    assert row["days_left"] == 7


def test_stratified_selection_not_naive_top_12_and_reliable_count(client, auth_headers, empty_db):
    """4 critical (urgency>=0.80) + 10 mid ([0.25,0.80)) + 5 fine (<0.25)
    reliable items, plus 1 unreliable item (only 2 purchases) and 1 high-
    urgency delivery-category item. Expected selection: top-2 critical +
    top-7 mid + top-3 fine = exactly 12 items, ordered by urgency DESC.

    This deliberately differs from a naive "top 12 by urgency overall"
    selection: two mid-tier items (urgency 0.45, 0.35) rank higher than
    every fine-tier item and would displace all of them under a naive
    top-12 — the stratification exists specifically so a "you're fine on
    this" item is never crowded out, which this test would catch if broken.
    """
    critical = {"crit_a": 0.95, "crit_b": 0.90, "crit_c": 0.85, "crit_d": 0.81}
    mid = {
        "mid_a": 0.79, "mid_b": 0.75, "mid_c": 0.70, "mid_d": 0.65, "mid_e": 0.60,
        "mid_f": 0.55, "mid_g": 0.50, "mid_h": 0.45, "mid_i": 0.35, "mid_j": 0.30,
    }
    fine = {"fine_a": 0.20, "fine_b": 0.15, "fine_c": 0.10, "fine_d": 0.05, "fine_e": 0.01}

    conn = sqlite3.connect(empty_db)
    for matched_id, urgency in {**critical, **mid, **fine}.items():
        _seed_reliable_item(conn, matched_id, urgency)
    # Unreliable: only 2 purchases -> must never appear, must not count toward reliable_count
    insert_purchase(conn, matched_id="unreliable_item", matched_category="Pantry",
                     raw_name="x", datetime=_iso(10), quantity=1, unit_price=1.0, total_price=1.0)
    insert_purchase(conn, matched_id="unreliable_item", matched_category="Pantry",
                     raw_name="x", datetime=_iso(5), quantity=1, unit_price=1.0, total_price=1.0)
    # High-urgency but delivery-category -> must never appear regardless of urgency
    _seed_reliable_item(conn, "delivery_high_urgency", urgency=50.0, category=DELIVERY_CATEGORY)
    conn.commit()
    conn.close()

    resp = client.get("/api/needed-soon", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()

    expected_order = (
        ["crit_a", "crit_b"] +
        ["mid_a", "mid_b", "mid_c", "mid_d", "mid_e", "mid_f", "mid_g"] +
        ["fine_a", "fine_b", "fine_c"]
    )
    actual_order = [row["matched_id"] for row in body["items"]]
    assert actual_order == expected_order

    # Excluded by the stratification cap despite outranking fine-tier items:
    excluded = {"crit_c", "crit_d", "mid_h", "mid_i", "mid_j", "fine_d", "fine_e"}
    assert excluded.isdisjoint(set(actual_order))

    assert "unreliable_item" not in actual_order
    assert "delivery_high_urgency" not in actual_order

    # 19 seeded reliable items (4 critical + 10 mid + 5 fine); unreliable_item
    # (2 purchases) doesn't count, but delivery_high_urgency IS reliable
    # (3 purchases) even though it's excluded from `items` by category -> 20.
    assert body["reliable_count"] == 20

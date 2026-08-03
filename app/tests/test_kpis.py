"""
GET /api/kpis (api/dashboard.py:44-92) — the headline dashboard numbers.

Methodology: every expected value here is computed by hand, in plain
arithmetic, from a purpose-built dataset of round numbers. Nothing is
re-derived by re-running the endpoint's own SQL — that would just re-validate
whatever bug might be there. All dates are computed relative to
date.today() at test-run time, never hardcoded, since the underlying SQL
filters on datetime >= date('now', ...).
"""

import sqlite3
from datetime import date, timedelta

from conftest import insert_purchase


def _iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def test_30d_core_metrics_hand_computed(client, auth_headers, empty_db):
    """One dataset exercising month_total, month_total_delta, orders (with both
    the same-source/same-date collapse and the different-source/same-date
    non-collapse), orders_delta (raw diff, not a %), avg_order/avg_order_delta,
    and tracked_items/tracked_items_delta all at once, with clean numbers.

    Current period (within last 30 days):
      - StoreA, today-5d: two line items $60 + $40 -> same (source,datetime)
        -> collapses into ONE order, item total $100
      - StoreB, today-5d: one line item $100 -> same datetime as StoreA's
        order but a DIFFERENT source -> must NOT collapse -> a second order
      cur_total = 200, cur_orders = 2, cur_tracked = 3 (item1, item2, item3)

    Previous period (30-60 days ago):
      - StoreA, today-45d: one line item $50 -> 1 order
      prev_total = 50, prev_orders = 1, prev_tracked = 1 (item4)
    """
    conn = sqlite3.connect(empty_db)
    d5 = _iso(5)
    insert_purchase(conn, raw_name="Item1", matched_id="item1", source="StoreA",
                     datetime=d5, quantity=1, unit_price=60.0, total_price=60.0)
    insert_purchase(conn, raw_name="Item2", matched_id="item2", source="StoreA",
                     datetime=d5, quantity=1, unit_price=40.0, total_price=40.0)
    insert_purchase(conn, raw_name="Item3", matched_id="item3", source="StoreB",
                     datetime=d5, quantity=1, unit_price=100.0, total_price=100.0)
    insert_purchase(conn, raw_name="Item4", matched_id="item4", source="StoreA",
                     datetime=_iso(45), quantity=1, unit_price=50.0, total_price=50.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/kpis?period=30d", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()

    # month_total: sum of current-period total_price
    assert body["month_total"] == 200
    # month_total_delta: (200-50)/50*100
    assert body["month_total_delta"] == 300.0

    # orders: DISTINCT source||datetime -> StoreA/d5 (collapsed) + StoreB/d5 (separate) = 2
    assert body["orders"] == 2
    # orders_delta: raw difference (2 - 1), NOT a percentage
    assert body["orders_delta"] == 1

    # avg_order = month_total / orders = 200/2 = 100; prev = 50/1 = 50
    assert body["avg_order"] == 100.0
    # avg_order_delta: (100-50)/50*100
    assert body["avg_order_delta"] == 100.0

    # tracked_items: distinct matched_id in current period = item1,item2,item3
    assert body["tracked_items"] == 3
    # tracked_items_delta: (3-1)/1*100
    assert body["tracked_items_delta"] == 200.0


def test_zero_previous_period_deltas_are_none_except_orders_delta(client, auth_headers, empty_db):
    """When the previous period has zero spend/orders/items, the three
    percentage-based delta fields must each independently guard against
    ZeroDivisionError and return None. orders_delta is the odd one out: it's
    a raw subtraction (cur_orders - prev_orders), so with prev_orders=0 it
    must still return a real number (cur_orders), not None — the endpoint
    would produce a divide-by-zero only if it treated this like the others."""
    conn = sqlite3.connect(empty_db)
    # Only current-period data; nothing in the 30-60-day-ago window at all.
    insert_purchase(conn, raw_name="Item1", matched_id="item1", source="StoreA",
                     datetime=_iso(5), quantity=1, unit_price=30.0, total_price=30.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/kpis?period=30d", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["month_total_delta"] is None
    assert body["avg_order_delta"] is None
    assert body["tracked_items_delta"] is None
    # Not a percentage field: prev_orders=0, cur_orders=1 -> raw diff = 1, not None
    assert body["orders_delta"] == 1
    assert body["orders_delta"] is not None


def test_period_all_is_ytd_vs_same_ytd_last_year(client, auth_headers, empty_db):
    """period=all takes a completely different code path: current calendar
    YTD vs the same YTD range last year, gated on strftime('%m-%d') <= today's
    month-day. Uses the earliest-possible previous-year month-day ("01-01",
    always <= today's cutoff) as the "included" case, and (on every day except
    Dec 31, where no later month-day exists to test against) the latest
    possible one ("12-31") as the "excluded" case.
    """
    today = date.today()
    cur_year = today.year
    prev_year = cur_year - 1

    conn = sqlite3.connect(empty_db)
    # Current year: exactly "today" -> trivially inside the YTD window.
    insert_purchase(conn, raw_name="A", matched_id="ytd_a", source="StoreYTD",
                     datetime=today.isoformat(), quantity=1, unit_price=100.0, total_price=100.0)
    # Previous year, Jan 1 -> "01-01" <= today's "%m-%d" is always true.
    insert_purchase(conn, raw_name="B", matched_id="ytd_b", source="StorePrev",
                     datetime=date(prev_year, 1, 1).isoformat(), quantity=1,
                     unit_price=40.0, total_price=40.0)
    is_dec_31 = today.month == 12 and today.day == 31
    if not is_dec_31:
        # Previous year, Dec 31 -> "12-31" > today's "%m-%d" on any other day,
        # so this must be excluded from the prior-YTD comparison window.
        insert_purchase(conn, raw_name="C", matched_id="ytd_c", source="StorePrev",
                         datetime=date(prev_year, 12, 31).isoformat(), quantity=1,
                         unit_price=999.0, total_price=999.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/kpis?period=all", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["month_total"] == 100
    assert body["orders"] == 1
    assert body["tracked_items"] == 1
    # prev_total only includes the Jan-1 row (40), never the excluded Dec-31 one (999)
    assert body["month_total_delta"] == 150.0  # (100-40)/40*100
    assert body["orders_delta"] == 0            # 1 - 1
    assert body["avg_order_delta"] == 150.0     # avg_order cur=100, prev=40
    assert body["tracked_items_delta"] == 0.0   # (1-1)/1*100

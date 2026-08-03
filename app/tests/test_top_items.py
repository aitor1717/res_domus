"""
GET /api/top-items (api/dashboard.py:334-366).

Every expected value is hand-computed plain arithmetic from a purpose-built
dataset, never re-derived by re-running the endpoint's own SQL. Dates are
relative to date.today() (coarse 5-day-old dates, well within any of the
30d/90d/all windows, so no UTC/local day-boundary sensitivity here).
"""

import sqlite3
from datetime import date, timedelta

from conftest import insert_purchase


def _iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def test_pct_is_percent_of_returned_top_10_only_not_of_all_spending(client, auth_headers, empty_db):
    """11 items with distinct spend amounts: 100,90,...,10 (top 10, sum=550)
    plus an 11th at 5 (outside the top 10, excluded entirely). `pct` is
    documented (see prompt for this test suite) to be % of the sum of the
    displayed top-10 only -- confirmed here by hand-computing against
    grand_total=550, not against 555 (which would include the excluded 11th
    item) or any other total."""
    amounts = [100, 90, 80, 70, 60, 50, 40, 30, 20, 10, 5]
    conn = sqlite3.connect(empty_db)
    for amt in amounts:
        insert_purchase(conn, matched_id=f"item{amt}", raw_name=f"item{amt}",
                         matched_category="Pantry", datetime=_iso(5),
                         quantity=1, unit_price=float(amt), total_price=float(amt))
    conn.commit()
    conn.close()

    resp = client.get("/api/top-items?period=30d", headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.get_json()

    assert len(rows) == 10
    assert "item5" not in {r["matched_id"] for r in rows}

    grand_total = 550  # sum of the top 10 only (100+90+...+10), by hand
    by_id = {r["matched_id"]: r for r in rows}
    assert by_id["item100"]["pct"] == round(100 / grand_total * 100)  # 18
    assert by_id["item10"]["pct"] == round(10 / grand_total * 100)    # 2

    # bar_width: % of the #1 item's own spend -> #1 is always 100
    assert by_id["item100"]["bar_width"] == 100
    assert by_id["item10"]["bar_width"] == round(10 / 100 * 100)  # 10


def test_delivery_category_excluded(client, auth_headers, empty_db):
    conn = sqlite3.connect(empty_db)
    insert_purchase(conn, matched_id="normal_item", raw_name="normal_item",
                     matched_category="Pantry", datetime=_iso(5),
                     quantity=1, unit_price=50.0, total_price=50.0)
    insert_purchase(conn, matched_id="courier_item", raw_name="courier_item",
                     matched_category="Delivery", datetime=_iso(5),
                     quantity=1, unit_price=500.0, total_price=500.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/top-items?period=30d", headers=auth_headers)
    rows = resp.get_json()
    assert {r["matched_id"] for r in rows} == {"normal_item"}

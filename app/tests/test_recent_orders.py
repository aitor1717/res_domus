"""
GET /api/recent-orders (api/dashboard.py:369-409).

Every expected value is hand-computed plain arithmetic from a purpose-built
dataset, never re-derived by re-running the endpoint's own SQL.
"""

import sqlite3
from datetime import date, timedelta

from conftest import insert_purchase


def _iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def test_grouping_by_datetime_and_source(client, auth_headers, empty_db):
    """Two line items sharing the same (datetime, source) must collapse into
    one order row: order_total = sum of their prices, item_count = 2."""
    d = _iso(3)
    conn = sqlite3.connect(empty_db)
    insert_purchase(conn, raw_name="Item A", matched_id="a", source="StoreX",
                     datetime=d, quantity=1, unit_price=30.0, total_price=30.0)
    insert_purchase(conn, raw_name="Item B", matched_id="b", source="StoreX",
                     datetime=d, quantity=1, unit_price=20.0, total_price=20.0)
    # A different source on the same date must NOT be merged into the above order.
    insert_purchase(conn, raw_name="Item C", matched_id="c", source="StoreY",
                     datetime=d, quantity=1, unit_price=99.0, total_price=99.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/recent-orders?period=30d", headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.get_json()
    assert len(rows) == 2

    by_source = {r["source"]: r for r in rows}
    assert by_source["StoreX"]["order_total"] == 50
    assert by_source["StoreX"]["item_count"] == 2
    assert by_source["StoreY"]["order_total"] == 99
    assert by_source["StoreY"]["item_count"] == 1


def test_entry_type_text_vs_image(client, auth_headers, empty_db):
    """entry_type is 'text' iff source_file == 'chat-entry', else 'image'."""
    conn = sqlite3.connect(empty_db)
    insert_purchase(conn, raw_name="Chat logged", matched_id="chat_item", source="ChatStore",
                     datetime=_iso(1), quantity=1, unit_price=15.0, total_price=15.0,
                     source_file="chat-entry")
    insert_purchase(conn, raw_name="Receipt scanned", matched_id="receipt_item", source="ReceiptStore",
                     datetime=_iso(2), quantity=1, unit_price=25.0, total_price=25.0,
                     source_file="02_ene_2026_groceries.csv")
    conn.commit()
    conn.close()

    resp = client.get("/api/recent-orders?period=30d", headers=auth_headers)
    rows = resp.get_json()
    by_source = {r["source"]: r for r in rows}
    assert by_source["ChatStore"]["entry_type"] == "text"
    assert by_source["ReceiptStore"]["entry_type"] == "image"

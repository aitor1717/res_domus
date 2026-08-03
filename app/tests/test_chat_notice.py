"""
GET /api/chat/notice -> api/chat.py's _get_notice() proactive session-opener
banner: a strict 4-tier priority check (budget>=80% -> stock 0-3 days left
-> price anomaly in last 7 days -> reorder urgency>=0.7), first match wins.

Every expected value/message is hand-computed plain arithmetic (or, for the
message text, a literal reproduction of the app's own f-string template,
which is presentation copy rather than the business logic under test) from a
purpose-built dataset, never re-derived by re-running the endpoint's own
query logic. Dates use utc_today() (see conftest.utc_today) wherever the
underlying SQL filters on 'now' (current month, "last 7 days", days-since-
last), since those are UTC-based.
"""

import calendar
import sqlite3
from datetime import date, timedelta

from conftest import insert_purchase, utc_today


def _iso(days_ago: int) -> str:
    return (utc_today() - timedelta(days=days_ago)).isoformat()


def _seed_urgent_item(conn, matched_id, urgency, category="Pantry"):
    """Same avg_interval=100 trick as test_needed_soon.py: 3 purchases 100
    days apart -> avg_interval_days=100 exactly, so
    reorder_urgency = days_since_last/100 = urgency exactly."""
    days_since_last = round(urgency * 100)
    for offset in (days_since_last + 200, days_since_last + 100, days_since_last):
        insert_purchase(conn, matched_id=matched_id, matched_category=category,
                         raw_name=matched_id, datetime=_iso(offset),
                         quantity=1, unit_price=1.0, total_price=1.0)


def _seed_budget(conn, pct: int, manual_budget: float = 100.0):
    """A manual budget of `manual_budget` and a today purchase totalling
    pct% of it -> pct_of_budget == pct exactly."""
    month = utc_today().strftime("%Y-%m")
    conn.execute("INSERT INTO budget (month, manual_budget) VALUES (?, ?)", (month, manual_budget))
    spend = manual_budget * pct / 100
    insert_purchase(conn, datetime=utc_today().isoformat(), quantity=1,
                     unit_price=spend, total_price=spend)


def _seed_low_stock_item(conn, matched_id, days_of_stock_left: int, category="Pantry"):
    """3 purchases of qty 10, spaced 10 days apart -> avg_interval=10,
    daily_consumption = 10/10 = 1.0. last purchase (10 - days_of_stock_left)
    days ago -> est_stock_remaining = 10 - 1*(10-days_of_stock_left) =
    days_of_stock_left, so days_of_stock_left = est/daily = itself exactly."""
    days_since_last = 10 - days_of_stock_left
    for offset in (days_since_last + 20, days_since_last + 10, days_since_last):
        insert_purchase(conn, matched_id=matched_id, matched_category=category,
                         raw_name=matched_id, datetime=_iso(offset),
                         quantity=10, unit_price=1.0, total_price=10.0)


def _seed_anomaly(conn, matched_id, days_ago: int):
    """20 baseline purchases at $2 (long in the past) + one $1000 outlier
    `days_ago` days back -> a clear (z=sqrt(20)~=4.47) anomaly."""
    for day in range(1, 21):
        insert_purchase(conn, matched_id=matched_id, raw_name="x",
                         datetime=_iso(200 + day), quantity=1, unit_price=2.0, total_price=2.0)
    insert_purchase(conn, matched_id=matched_id, raw_name="x",
                     datetime=_iso(days_ago), quantity=1, unit_price=1000.0, total_price=1000.0)


def _local_days_left() -> int:
    """_get_notice's own days_left computation uses Python's local
    date.today(), not UTC -- reproduced here (not the metric under test,
    just matching the app's own presentation text) so the expected message
    string is exact."""
    today = date.today()
    return calendar.monthrange(today.year, today.month)[1] - today.day


def test_budget_tier_wins_over_every_other_simultaneously_triggered_tier(client, auth_headers, empty_db):
    """All four tiers' trigger conditions present at once: budget at 85%,
    an item with 2 days of stock left, a price anomaly 2 days ago, and an
    item at reorder_urgency 0.9. Only the budget (highest-priority) message
    must come back."""
    conn = sqlite3.connect(empty_db)
    # A very large budget swamps the incidental current-month contribution
    # from the other three tiers' seeded purchases (e.g. the anomaly
    # outlier), which would otherwise nudge pct_of_budget by a fraction of a
    # percent and risk flipping the truncated display value.
    _seed_budget(conn, pct=85, manual_budget=1_000_000.0)
    _seed_low_stock_item(conn, "low_stock_item", days_of_stock_left=2)
    _seed_anomaly(conn, "anomaly_item", days_ago=2)
    _seed_urgent_item(conn, "urgent_item", urgency=0.9)
    conn.commit()
    conn.close()

    resp = client.get("/api/chat/notice?lang=en", headers=auth_headers)
    assert resp.status_code == 200
    notice = resp.get_json()["notice"]
    assert notice == f"Budget at 85%, {_local_days_left()} days remaining."


def test_budget_threshold_boundary(client, auth_headers, empty_db):
    conn = sqlite3.connect(empty_db)
    _seed_budget(conn, pct=80)
    conn.commit()
    conn.close()
    resp = client.get("/api/chat/notice?lang=en", headers=auth_headers)
    assert resp.get_json()["notice"] == f"Budget at 80%, {_local_days_left()} days remaining."

    conn = sqlite3.connect(empty_db)
    conn.execute("DELETE FROM purchases")
    conn.execute("DELETE FROM budget")
    _seed_budget(conn, pct=79)
    conn.commit()
    conn.close()
    resp = client.get("/api/chat/notice?lang=en", headers=auth_headers)
    body = resp.get_json()
    assert body["notice"] is None or "Budget at" not in (body["notice"] or "")


def test_stock_tier_triggers_between_0_and_3_days_when_budget_quiet(client, auth_headers, empty_db):
    conn = sqlite3.connect(empty_db)
    _seed_low_stock_item(conn, "low_stock_item", days_of_stock_left=3)
    conn.commit()
    conn.close()

    resp = client.get("/api/chat/notice?lang=en", headers=auth_headers)
    notice = resp.get_json()["notice"]
    assert notice == "You'll run out of low_stock_item in 3 days."


def test_stock_tier_does_not_trigger_at_4_days(client, auth_headers, empty_db):
    conn = sqlite3.connect(empty_db)
    _seed_low_stock_item(conn, "ok_stock_item", days_of_stock_left=4)
    conn.commit()
    conn.close()

    resp = client.get("/api/chat/notice?lang=en", headers=auth_headers)
    notice = resp.get_json()["notice"]
    assert notice is None or "run out" not in notice


def test_anomaly_tier_triggers_within_last_7_days_when_higher_tiers_quiet(client, auth_headers, empty_db):
    conn = sqlite3.connect(empty_db)
    _seed_anomaly(conn, "anomaly_item", days_ago=6)
    conn.commit()
    conn.close()

    resp = client.get("/api/chat/notice?lang=en", headers=auth_headers)
    notice = resp.get_json()["notice"]
    assert notice is not None
    assert "anomaly_item" in notice
    assert "unusually high" in notice


def test_urgency_tier_threshold_boundary(client, auth_headers, empty_db):
    conn = sqlite3.connect(empty_db)
    _seed_urgent_item(conn, "borderline_urgent", urgency=0.70)
    conn.commit()
    conn.close()
    resp = client.get("/api/chat/notice?lang=en", headers=auth_headers)
    notice = resp.get_json()["notice"]
    assert notice is not None and "borderline_urgent" in notice

    conn = sqlite3.connect(empty_db)
    conn.execute("DELETE FROM purchases")
    _seed_urgent_item(conn, "not_urgent_enough", urgency=0.69)
    conn.commit()
    conn.close()
    resp = client.get("/api/chat/notice?lang=en", headers=auth_headers)
    notice = resp.get_json()["notice"]
    assert notice is None or "not_urgent_enough" not in notice

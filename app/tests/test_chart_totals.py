"""
GET /api/chart (api/dashboard.py:134-265), period=30d/90d bucket path
(BUCKET_DAYS=2.5, bucket = floor(julianday(datetime)/2.5)).

Every expected value is hand-computed plain arithmetic from a purpose-built
dataset, never re-derived by re-running the endpoint's own SQL. Dates are
relative to date.today() at test-run time.

period=90d shares the exact same bucket-width/query-shape code path as 30d
(same SQL template, only the window-length `days` substitution differs --
see api/dashboard.py's chart(), and CLAUDE.md's note that "30d and 90d
differ only in window length"), so exercising 30d's bucket math is treated
as sufficient coverage of both; not re-tested separately here.

JULIAN_OFFSET below is an independent, verifiable astronomical/calendar
constant (SQLite's julianday('2000-01-01') == 2451544.5, and
date(2000,1,1).toordinal() == 730120; the difference is fixed for every
date), not a re-derivation of any app logic -- it lets the test compute
each date's expected bucket number using only Python's stdlib.
"""

import sqlite3
from datetime import date, timedelta

from conftest import insert_purchase

JULIAN_OFFSET = 1721424.5
BUCKET_DAYS = 2.5


def _iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _julian_day(d: date) -> float:
    return d.toordinal() + JULIAN_OFFSET


def _bucket_of(d: date) -> int:
    return int(_julian_day(d) // BUCKET_DAYS)


def _sql_current_bucket(db_path) -> int:
    """The endpoint's own notion of 'now's bucket, per its exact SQL
    (CAST(julianday('now')/2.5 AS INTEGER)). SQLite's 'now' is UTC, so on a
    test machine whose local date differs from the UTC date, date.today()'s
    bucket can be off by one from this. Reading it directly here is an
    environment fact (what time is it right now), not a re-derivation of the
    drop LOGIC under test -- it's used only to correctly place a synthetic
    row into "the bucket the endpoint currently considers trailing", exactly
    like using calendar.monthrange elsewhere in this suite."""
    conn = sqlite3.connect(db_path)
    val = conn.execute("SELECT CAST(julianday('now') / 2.5 AS INTEGER)").fetchone()[0]
    conn.close()
    return val


def _find_date_for_bucket(target_bucket: int) -> date:
    for offset in range(-3, 4):
        d = date.today() + timedelta(days=offset)
        if _bucket_of(d) == target_bucket:
            return d
    raise AssertionError(f"no date within 3 days of today maps to bucket {target_bucket}")


def test_category_composition_and_delivery_separation(client, auth_headers, empty_db):
    """One bucket, four categories: groceries (Pantry), meat (Carnes), a third
    "other" category (Produce, not in the groceries/meat/delivery sets), and
    a delivery purchase. All dated 10 days ago -- safely inside a completed
    bucket, far from the in-progress "today" bucket, so the trailing-bucket
    drop logic (tested separately below) never interferes here.
    """
    conn = sqlite3.connect(empty_db)
    d = _iso(10)
    insert_purchase(conn, raw_name="Groceries", matched_id="groc1", matched_category="Pantry",
                     datetime=d, quantity=1, unit_price=100.0, total_price=100.0)
    insert_purchase(conn, raw_name="Meat", matched_id="meat1", matched_category="Carnes",
                     datetime=d, quantity=1, unit_price=50.0, total_price=50.0)
    insert_purchase(conn, raw_name="Produce", matched_id="prod1", matched_category="Produce",
                     datetime=d, quantity=1, unit_price=30.0, total_price=30.0)
    insert_purchase(conn, raw_name="Courier fee", matched_id="deliv1", matched_category="Delivery",
                     datetime=d, quantity=1, unit_price=20.0, total_price=20.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/chart?period=30d", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()

    assert len(body["labels"]) == 1  # everything in one 2.5-day bucket
    assert body["groceries"][0] == 100
    assert body["meat"][0] == 50
    # total excludes delivery: groceries + meat + produce = 100+50+30 = 180
    assert body["total"][0] == 180
    assert body["delivery"][0] == 20
    # "other" is the non-groceries/non-meat remainder of total -> the Produce row
    assert body["other"][0] == 30
    # deliveryAbove = total + delivery, exactly
    assert body["deliveryAbove"][0] == body["total"][0] + body["delivery"][0] == 200


def test_bucket_boundary_matches_hand_computed_julian_day_formula(client, auth_headers, empty_db):
    """Seven consecutive days (today-20 .. today-14), each with a $100
    grocery purchase, all safely earlier than today's in-progress bucket.
    Independently compute each day's bucket via floor(julianday/2.5) using
    only Python's stdlib (see _bucket_of), group the $100s by predicted
    bucket, and confirm the endpoint's totals match that grouping exactly --
    this is what would catch an off-by-one in the bucket-boundary formula.
    """
    conn = sqlite3.connect(empty_db)
    days = list(range(20, 13, -1))  # 20,19,...,14 (oldest to newest)
    expected_by_bucket: dict[int, float] = {}
    for n in days:
        d = date.today() - timedelta(days=n)
        insert_purchase(conn, raw_name=f"Day-{n}", matched_id=f"item_{n}", matched_category="Pantry",
                         datetime=d.isoformat(), quantity=1, unit_price=100.0, total_price=100.0)
        b = _bucket_of(d)
        expected_by_bucket[b] = expected_by_bucket.get(b, 0) + 100
    conn.commit()
    conn.close()

    expected_totals = [expected_by_bucket[b] for b in sorted(expected_by_bucket)]

    resp = client.get("/api/chart?period=30d", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()

    assert len(expected_totals) > 1  # sanity: the 7-day span really does straddle >1 bucket
    assert body["total"] == expected_totals
    assert body["groceries"] == expected_totals  # everything here is groceries -> other/meat are 0


def test_trailing_incomplete_bucket_is_dropped_when_more_than_one_exists(client, auth_headers, empty_db):
    """A completed bucket (5 days ago, $50) plus a row in whatever bucket the
    endpoint currently considers trailing ($999): the current
    (still-accumulating) bucket must be dropped so the chart never shows a
    fake "spending fell off a cliff" point for a window that hasn't finished
    yet."""
    trailing_date = _find_date_for_bucket(_sql_current_bucket(empty_db))
    completed_date = date.today() - timedelta(days=5)
    assert _bucket_of(completed_date) != _bucket_of(trailing_date)  # sanity: 2 distinct buckets

    conn = sqlite3.connect(empty_db)
    insert_purchase(conn, datetime=completed_date.isoformat(), quantity=1, unit_price=50.0, total_price=50.0)
    insert_purchase(conn, datetime=trailing_date.isoformat(), quantity=1, unit_price=999.0, total_price=999.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/chart?period=30d", headers=auth_headers)
    body = resp.get_json()

    assert len(body["labels"]) == 1
    assert body["total"] == [50]  # the $999 trailing bucket must not appear at all


def test_trailing_bucket_not_dropped_when_it_is_the_only_bucket(client, auth_headers, empty_db):
    """The 'never return an empty chart' guarantee: when the only data is in
    the current (in-progress) bucket, it must NOT be dropped -- otherwise the
    chart would render as completely empty."""
    trailing_date = _find_date_for_bucket(_sql_current_bucket(empty_db))

    conn = sqlite3.connect(empty_db)
    insert_purchase(conn, datetime=trailing_date.isoformat(), quantity=1, unit_price=42.0, total_price=42.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/chart?period=30d", headers=auth_headers)
    body = resp.get_json()

    assert len(body["labels"]) == 1
    assert body["total"] == [42]

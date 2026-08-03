"""
GET /api/budget (api/dashboard.py:95-131) + v_budget (parser/build_db.py).

Every expected value is hand-computed plain arithmetic from a purpose-built,
round-number dataset — never re-derived by re-running the endpoint's own SQL.
All dates are relative to date.today() at test-run time.

deviation_pct is the metric that shipped a real, live sign-flip bug (see
CLAUDE.md's "Fix budget/deviation ring gauges" note): it used to compare
current spend against the 18-month avg_baseline, but that baseline includes
the current (still-accumulating) month, which mostly overlaps the very
last-30-days window being measured — so recent spending partially inflated
the number it was being compared against, and could flip the sign of the
result. The fix compares two adjacent, non-overlapping 30-day windows
instead. test_deviation_pct_hand_computed_and_would_flip_sign_under_old_logic
below builds a dataset where the two approaches disagree on the SIGN, not
just the magnitude, to prove this suite would actually have caught the bug.
"""

import calendar
import sqlite3
from datetime import date, timedelta

from conftest import insert_purchase


def _iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _months_ago(d: date, n: int) -> date:
    """n calendar months before d, clamping the day to the target month's
    length (e.g. months_ago(2026-03-31, 1) -> 2026-02-28)."""
    y = d.year
    mo = d.month - n
    while mo <= 0:
        mo += 12
        y -= 1
    day = min(d.day, calendar.monthrange(y, mo)[1])
    return date(y, mo, day)


def test_spent_this_month_excludes_prior_calendar_month(client, auth_headers, empty_db):
    """spent_this_month must be the current CALENDAR month only — a purchase
    made last calendar month, even if it's within the last 30 days, must be
    excluded."""
    today = date.today()
    last_day_prev_month = date(today.year, today.month, 1) - timedelta(days=1)

    conn = sqlite3.connect(empty_db)
    insert_purchase(conn, datetime=today.isoformat(), quantity=1,
                     unit_price=70.0, total_price=70.0)
    insert_purchase(conn, datetime=last_day_prev_month.isoformat(), quantity=1,
                     unit_price=999.0, total_price=999.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/budget", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.get_json()["spent_this_month"] == 70


def test_avg_baseline_is_equal_weight_per_month(client, auth_headers, empty_db):
    """avg_baseline must be the mean of each month's TOTAL, one value per
    month regardless of how many purchases (or days) made it up — not spend
    divided across all rows."""
    today = date.today()
    conn = sqlite3.connect(empty_db)
    # Month n=2: one $30 purchase -> month_total = 30
    insert_purchase(conn, datetime=_months_ago(today, 2).isoformat(),
                     quantity=1, unit_price=30.0, total_price=30.0)
    # Month n=3: two $30 purchases -> month_total = 60
    insert_purchase(conn, datetime=_months_ago(today, 3).isoformat(),
                     quantity=1, unit_price=30.0, total_price=30.0)
    insert_purchase(conn, datetime=_months_ago(today, 3).isoformat(),
                     quantity=1, unit_price=30.0, total_price=30.0)
    # Month n=4: three $30 purchases -> month_total = 90
    for _ in range(3):
        insert_purchase(conn, datetime=_months_ago(today, 4).isoformat(),
                         quantity=1, unit_price=30.0, total_price=30.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/budget", headers=auth_headers)
    assert resp.status_code == 200
    # Mean of (30, 60, 90) = 60 -- NOT (30+60+90)/6=30 (which is what a
    # per-row, rather than per-month, average would give.
    assert resp.get_json()["avg_baseline"] == 60.0


def test_effective_budget_prefers_manual_override_else_falls_back(client, auth_headers, empty_db):
    today = date.today()
    conn = sqlite3.connect(empty_db)
    insert_purchase(conn, datetime=_months_ago(today, 2).isoformat(),
                     quantity=1, unit_price=50.0, total_price=50.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/budget", headers=auth_headers)
    body = resp.get_json()
    assert body["avg_baseline"] == 50.0
    assert body["effective_budget"] == 50.0
    assert body["manual_override"] is None

    conn = sqlite3.connect(empty_db)
    conn.execute("INSERT INTO budget (month, manual_budget) VALUES (?, ?)",
                 (today.strftime("%Y-%m"), 777.0))
    conn.commit()
    conn.close()

    resp = client.get("/api/budget", headers=auth_headers)
    body = resp.get_json()
    assert body["effective_budget"] == 777.0
    assert body["manual_override"] == 777.0
    assert body["avg_baseline"] == 50.0  # unchanged -- override doesn't affect the baseline itself


def test_pct_of_budget(client, auth_headers, empty_db):
    today = date.today()
    conn = sqlite3.connect(empty_db)
    conn.execute("INSERT INTO budget (month, manual_budget) VALUES (?, ?)",
                 (today.strftime("%Y-%m"), 200.0))
    insert_purchase(conn, datetime=today.isoformat(), quantity=1,
                     unit_price=50.0, total_price=50.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/budget", headers=auth_headers)
    assert resp.get_json()["pct_of_budget"] == 25.0  # 50/200*100


def test_days_remaining_matches_calendar_monthrange(client, auth_headers, empty_db):
    today = date.today()
    expected = calendar.monthrange(today.year, today.month)[1] - today.day

    resp = client.get("/api/budget", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.get_json()["days_remaining"] == expected


def test_deviation_pct_none_when_prev_30d_zero(client, auth_headers, empty_db):
    conn = sqlite3.connect(empty_db)
    insert_purchase(conn, datetime=_iso(5), quantity=1, unit_price=80.0, total_price=80.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/budget", headers=auth_headers)
    body = resp.get_json()
    assert body["last_30d_spend"] == 80
    assert body["deviation_pct"] is None


def test_deviation_pct_hand_computed_and_would_flip_sign_under_old_logic(client, auth_headers, empty_db):
    """
    Dataset:
      - today:          $100  (in last_30d window AND in the current calendar month)
      - 45 days ago:    $300  (in prev_30d window: [60d ago, 30d ago) )
      - 14 months ago, n=3..16 (one purchase each): $20  (outside both 30-day
        windows, but inside the 18-month avg_baseline lookback with a safety
        margin -- SQLite's 'now', '-18 months' modifier doesn't land on
        exactly the same date as a naive calendar-month subtraction would, so
        n is kept a few months short of the true 18-month edge to avoid that
        boundary entirely rather than fight it)

    New (correct) formula: deviation_pct compares last_30d ($100) against the
    immediately-preceding, non-overlapping 30-day window, prev_30d ($300):
        (100 - 300) / 300 * 100 = -66.7%   (spending genuinely dropped)

    Old (buggy) formula, hand-computed here from the SAME data purely for
    comparison (never asserted as what the endpoint returns): last_30d
    against avg_baseline, the 18-month average INCLUDING the current
    month's own $100:
        avg_baseline = (100 + 300 + 14*20) / 16 = 680/16 = 42.5
        (100 - 42.5) / 42.5 * 100 = +135.3%      (reads as spending UP)

    The two formulas disagree on the *sign*, not just the magnitude -- this
    is exactly the class of bug that shipped live. If the endpoint ever
    regresses back to the old baseline-based comparison, this test flips
    from a negative to a positive deviation_pct and fails.
    """
    today = date.today()
    conn = sqlite3.connect(empty_db)
    insert_purchase(conn, datetime=today.isoformat(), quantity=1,
                     unit_price=100.0, total_price=100.0)
    insert_purchase(conn, datetime=_iso(45), quantity=1,
                     unit_price=300.0, total_price=300.0)
    for n in range(3, 17):  # 14 distinct months
        insert_purchase(conn, datetime=_months_ago(today, n).isoformat(),
                         quantity=1, unit_price=20.0, total_price=20.0)
    conn.commit()
    conn.close()

    resp = client.get("/api/budget", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["last_30d_spend"] == 100
    assert body["avg_baseline"] == 42.5  # sanity-check the premise of the old-style calc below

    new_deviation = round((100 - 300) / 300 * 100, 1)
    assert new_deviation == -66.7
    assert body["deviation_pct"] == new_deviation

    old_style_would_have_been = round((100 - body["avg_baseline"]) / body["avg_baseline"] * 100, 1)
    assert old_style_would_have_been == 135.3

    assert body["deviation_pct"] < 0
    assert old_style_would_have_been > 0

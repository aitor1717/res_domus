"""
v_anomalies (parser/build_db.py:129-141) — a view, not an endpoint, so it's
queried directly against the real DB (the methodology's "or real SQL view"
option).

Every expected value is hand-computed plain arithmetic from a purpose-built
dataset, never re-derived by re-running the view's own SQL. Dates use round
recent offsets (not exact-boundary-sensitive), so plain date.today() is fine
here (unlike test_item_stats.py/test_needed_soon.py, nothing here depends on
an exact 'now'-based day count).
"""

import sqlite3
from datetime import date, timedelta

from conftest import insert_purchase


def _iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _anomaly_row(db_path, matched_id) -> dict | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM v_anomalies WHERE matched_id = ?", (matched_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def test_z_score_and_direction_hand_computed(client, auth_headers, empty_db):
    """20 purchases at a constant price v=2.0, plus one outlier. For a set of
    n identical baseline points plus one differing point X, the population
    z-score of X against the full set (itself included) is exactly
    sign(X-v) * sqrt(n) regardless of how far X is from v -- a closed-form
    result, not something read off the SQL. sqrt(20) = 4.4721..., rounded to
    2dp by the view = 4.47.
    """
    conn = sqlite3.connect(empty_db)
    for day in range(1, 21):
        insert_purchase(conn, matched_id="high_outlier_item", raw_name="x",
                         datetime=_iso(200 + day), quantity=1, unit_price=2.0, total_price=2.0)
    insert_purchase(conn, matched_id="high_outlier_item", raw_name="x",
                     datetime=_iso(5), quantity=1, unit_price=1000.0, total_price=1000.0)

    for day in range(1, 21):
        insert_purchase(conn, matched_id="low_outlier_item", raw_name="x",
                         datetime=_iso(200 + day), quantity=1, unit_price=10.0, total_price=10.0)
    insert_purchase(conn, matched_id="low_outlier_item", raw_name="x",
                     datetime=_iso(5), quantity=1, unit_price=1.0, total_price=1.0)
    conn.commit()
    conn.close()

    import math
    expected_abs_z = round(math.sqrt(20), 2)  # 4.47

    high_row = _anomaly_row(empty_db, "high_outlier_item")
    assert high_row is not None
    assert high_row["direction"] == "high"
    assert high_row["z_score"] == expected_abs_z

    low_row = _anomaly_row(empty_db, "low_outlier_item")
    assert low_row is not None
    assert low_row["direction"] == "low"
    assert low_row["z_score"] == -expected_abs_z


def test_abs_z_score_threshold_boundary(client, auth_headers, empty_db):
    """Two items, each 5 purchases at 9.0 + 5 at 11.0 (population mean=10,
    std=1 before the 11th, outlier row) plus one more outlier purchase whose
    price was solved for (via independent Python arithmetic, not SQL -- see
    the comment below) so that the FULL 11-point population z-score for that
    outlier lands at exactly z=2.99 for one item and z=3.01 for the other.
    The view's filter is `ABS(z) > 3` (strict): 2.99 must be excluded, 3.01
    must be included -- this is exactly the kind of off-by-one (> vs >=)
    that this suite exists to catch.

    Prices were solved with: baseline=[9.0]*5+[11.0]*5; find X such that
    round((X - round(mean_incl_X,4)) / round(std_incl_X,4), 2) == target,
    using the exact same population-stdev formula the view uses (plain
    arithmetic, not a re-run of the app's SQL) -- verified independently
    against a real SQLite connection before being hardcoded here.
    """
    conn = sqlite3.connect(empty_db)
    for matched_id, outlier_price in (
        ("boundary_excluded_2_99", 19.483776),
        ("boundary_included_3_01", 20.120409),
    ):
        for price in (9.0, 9.0, 9.0, 9.0, 9.0, 11.0, 11.0, 11.0, 11.0, 11.0):
            insert_purchase(conn, matched_id=matched_id, raw_name="x",
                             datetime=_iso(100), quantity=1, unit_price=price, total_price=price)
        insert_purchase(conn, matched_id=matched_id, raw_name="x",
                         datetime=_iso(5), quantity=1, unit_price=outlier_price, total_price=outlier_price)
    conn.commit()
    conn.close()

    excluded = _anomaly_row(empty_db, "boundary_excluded_2_99")
    included = _anomaly_row(empty_db, "boundary_included_3_01")

    assert excluded is None  # z=2.99 <= 3 -> not anomalous, no row at all
    assert included is not None
    assert included["z_score"] == 3.01
    assert included["direction"] == "high"

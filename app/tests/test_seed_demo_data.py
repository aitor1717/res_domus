"""
Regression test for the 2026-08-08 audit's finding: scripts/seed_demo_data.py
wrote matched_id as the bare aux_items.csv row id instead of the resolved
item name, so every demo-seeded purchase displayed a raw number ("47")
instead of an item name ("Papa") wherever matched_id is rendered directly
(Running Low, Top Spend, Register, chat notice).
"""

from scripts.seed_demo_data import build_trips, load_items


def test_matched_id_matches_raw_name_resolution():
    items = load_items()
    trips = build_trips(items)

    rows = [row for _, _, trip_rows in trips for row in trip_rows]
    assert rows, "seed_demo_data produced no rows to check"

    for row in rows:
        assert row["matched_id"] == row["raw_name"]

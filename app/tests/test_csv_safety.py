"""
Regression tests for the 2026-08-11 audit's CSV-formula-injection finding
(app/csv_safety.py). export_xlsx() was fixed first (see test_settings.py);
this covers the two other CSV writers carrying the same untrusted-text-into-
a-spreadsheet-openable-file risk: parser/grocery_parser.py's save_review()
(writes review/*.csv from OCR'd receipt items) and api/items.py's _save()
(covered in test_items.py, not here).

Both write paths sanitize on write and are read back by the app itself
(parser/build_db.py's _import_csv() re-imports review/*.csv into the DB;
load_canonical_items() re-reads aux_items.csv for the parser's own item-
matching prompt), so a matching desanitize on every read is required too -
otherwise the protective leading quote would leak into the database or into
Claude's prompt. These tests exercise the full write->read roundtrip, not
just the sanitizer in isolation, since that's where a mismatched pair of
fixes would actually show up.
"""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from csv_safety import desanitize_cell, sanitize_cell
from parser.build_db import SCHEMA, VIEWS, run_import
from parser.grocery_parser import load_canonical_items, save_review


def test_sanitize_only_prefixes_formula_trigger_chars():
    assert sanitize_cell("=cmd|'/c calc'!A1") == "'=cmd|'/c calc'!A1"
    assert sanitize_cell("+1") == "'+1"
    assert sanitize_cell("-1") == "'-1"
    assert sanitize_cell("@SUM(1,1)") == "'@SUM(1,1)"
    assert sanitize_cell("Milk") == "Milk"  # no trigger char - untouched
    assert sanitize_cell(4.5) == 4.5        # non-string - untouched
    assert sanitize_cell(None) is None


def test_desanitize_only_strips_its_own_prefix():
    assert desanitize_cell("'=cmd|'/c calc'!A1") == "=cmd|'/c calc'!A1"
    assert desanitize_cell("Milk") == "Milk"           # nothing to strip
    assert desanitize_cell("'tis a name") == "'tis a name"  # not a guard prefix
    assert desanitize_cell(4.5) == 4.5


@pytest.mark.parametrize("raw_name", ["=cmd|'/c calc'!A1", "+1+1", "-2+3", "@SUM(A1:A2)"])
def test_save_review_then_run_import_roundtrip_is_clean_in_db_but_guarded_on_disk(
    raw_name, flask_app, tmp_path
):
    review_dir = Path(flask_app.config["REVIEW_DIR"])
    db_path = Path(flask_app.config["DB_PATH"])

    items = [{
        "raw_name": raw_name, "matched_id": "formula_test", "matched_category": "Pantry",
        "matched_subcategory": None, "tags": None, "unit": "u", "quantity": 1,
        "unit_price": 2.5, "total_price": 2.5, "source": "@EVIL()",
        "order_id": None, "payment_method": None, "datetime": "2026-01-05",
        "gpt_notes": None,
    }]
    csv_path = save_review("audit_test", date(2026, 1, 5), items, [], review_dir)

    # Guarded on disk - safe if this file is ever opened directly in Excel.
    on_disk = csv_path.read_text(encoding="utf-8")
    assert f"'{raw_name}" in on_disk
    assert "'@EVIL()" in on_disk

    # Caller's own `items` list must be untouched - save_review sanitizes
    # copies, not the list the frontend/dedup logic still uses.
    assert items[0]["raw_name"] == raw_name

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.executescript(VIEWS)
    conn.commit()
    conn.close()

    run_import(db_path, review_dir)

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT raw_name, source FROM purchases WHERE matched_id = 'formula_test'"
    ).fetchone()
    conn.close()

    # Clean in the database - the guard prefix never leaks into stored data.
    assert row == (raw_name, "@EVIL()")


def test_load_canonical_items_desanitizes_names_written_by_the_items_page(tmp_path):
    """aux_items.csv as api/items.py's _save() would actually write it for a
    name starting with a formula-trigger char - load_canonical_items() (used
    to build the parser's item-matching prompt) must see the clean name, not
    the guarded one."""
    import csv

    csv_path = tmp_path / "aux_items.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["id", "item", "unit", "category", "subcategory", "synonyms", "notes", "tags"]
        )
        writer.writeheader()
        writer.writerow({
            "id": "1", "item": sanitize_cell("=Evil Item"), "unit": "u",
            "category": "Pantry", "subcategory": "", "synonyms": "", "notes": "", "tags": "",
        })

    items = load_canonical_items(csv_path)

    assert items[0]["id"] == "=Evil Item"

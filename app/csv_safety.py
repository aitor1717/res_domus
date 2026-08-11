"""
Shared CSV/spreadsheet formula-injection guard.

Used anywhere text of untrusted origin - OCR'd receipt text, or any value a
user could later open in Excel/Sheets - gets written into a CSV or xlsx
cell. A value starting with =, +, -, or @ is a formula trigger in those
apps; the standard mitigation (OWASP) is a leading single quote, which they
render as literal text instead of evaluating.

Files this app writes fall into two groups:
  - user-facing exports (api/settings.py's export_xlsx) - sanitize_cell()
    on write is the whole fix, nothing reads the file back.
  - internal state re-read by the app itself (review/*.csv, re-imported by
    parser/build_db.py; aux_items.csv, re-read by api/items.py and
    parser/grocery_parser.py's load_canonical_items) - sanitize_cell() on
    write, desanitize_cell() on every read back into the app, so the
    protective prefix never leaks into the database or the Items page UI.
"""

FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@")


def sanitize_cell(val):
    if isinstance(val, str) and val.startswith(FORMULA_TRIGGER_CHARS):
        return "'" + val
    return val


def desanitize_cell(val):
    if isinstance(val, str) and val[:1] == "'" and val[1:].startswith(FORMULA_TRIGGER_CHARS):
        return val[1:]
    return val

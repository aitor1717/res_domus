"""
Static regression guard for the 2026-07-12 stored-XSS finding (see CLAUDE.md):
a matched_id value was rendered unescaped via innerHTML in the dashboard.
The fix was a shared esc() helper (static/js/utils.js) applied at every point
an AI-parsed or user-editable field is interpolated into a template literal.

This can't drive a real browser, so it's a static heuristic, not a full
guarantee — it scans every page script for `${...}` interpolations of known
server-sourced string fields and asserts each one is escaped, either via
esc(...) or the JSON.stringify(...).replace(/"/g, ...) idiom used once in
items.js for JS-string-inside-an-HTML-attribute context (checked by hand:
safe there because entity-decoding happens before the JS engine sees the
string, and quote breakout is what's neutralized — see PR discussion).
"""

import re
from pathlib import Path

JS_DIR = Path(__file__).resolve().parent.parent / "static" / "js"

# Fields that come from receipt parsing, chat purchase-logging, or manual
# item-library/register edits — i.e. attacker-controllable via an AI parse
# or a form field, as opposed to internally-computed numbers/dates.
RISKY_FIELDS = [
    "raw_name", "matched_id", "matched_category", "matched_subcategory",
    "synonyms", "subcategory", "gpt_notes", "source", "unit", "item", "tags",
]

INTERPOLATION_RE = re.compile(r"\$\{([^{}]*)\}")
LENGTH_ACCESS_RE = re.compile(r"\([^()]*\)\.length\b|\b\w+\.length\b")
SAFE_PREFIX_RE = re.compile(r"^\s*esc\(")
SAFE_JSON_ATTR_RE = re.compile(r"^\s*JSON\.stringify\([^)]*\)\.replace\(/\"/g,")


def _risky_unescaped_interpolations(text: str) -> list[str]:
    violations = []
    for m in INTERPOLATION_RE.finditer(text):
        expr = m.group(1)
        # .length checks never render the field's content, only its size.
        stripped = LENGTH_ACCESS_RE.sub("", expr)
        touches_risky_field = any(
            re.search(rf"\.{re.escape(field)}\b", stripped) for field in RISKY_FIELDS
        )
        if not touches_risky_field:
            continue
        if SAFE_PREFIX_RE.match(expr) or SAFE_JSON_ATTR_RE.match(expr):
            continue
        violations.append(expr.strip())
    return violations


def test_no_unescaped_risky_fields_in_page_scripts():
    offenders = {}
    for path in sorted(JS_DIR.glob("*.js")):
        if path.name == "utils.js":
            continue
        violations = _risky_unescaped_interpolations(path.read_text())
        if violations:
            offenders[path.name] = violations
    assert not offenders, f"Unescaped server-sourced fields found: {offenders}"


def test_heuristic_actually_detects_a_known_bad_pattern():
    """Guards the guard: confirms the detector isn't vacuously passing."""
    bad = "el.innerHTML = `<span>${item.raw_name}</span>`;"
    assert _risky_unescaped_interpolations(bad) == ["item.raw_name"]

    good = "el.innerHTML = `<span>${esc(item.raw_name)}</span>`;"
    assert _risky_unescaped_interpolations(good) == []


def test_utils_esc_helper_escapes_html_special_chars():
    text = (Path(__file__).resolve().parent.parent / "static" / "js" / "utils.js").read_text()
    assert "function esc(" in text
    for char in ("&", '"', "<", ">"):
        assert char in text, f"esc() no longer appears to handle {char!r}"

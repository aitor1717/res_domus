"""
Register page — lists actual purchase entries within a date range, similar
in spirit to the items library but showing real rows instead of canon items.
"""

import sqlite3
from flask import Blueprint, jsonify, request, current_app

bp = Blueprint("register", __name__, url_prefix="/api/register")


def _db():
    return sqlite3.connect(current_app.config["DB_PATH"])


def _rows_as_dicts(cur: sqlite3.Cursor) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


@bp.get("/entries")
def entries():
    start = request.args.get("start")
    end = request.args.get("end")
    q = (request.args.get("q") or "").strip().lower()

    conn = _db()
    sql = """
        SELECT id, datetime, raw_name, matched_id, matched_category, matched_subcategory,
               quantity, unit, unit_price, total_price, source, gpt_notes
        FROM purchases
        WHERE raw_name != 'TOTAL'
    """
    params: list = []
    if start:
        sql += " AND datetime >= ?"
        params.append(start)
    if end:
        sql += " AND datetime <= ?"
        params.append(end)
    if q:
        sql += " AND (LOWER(raw_name) LIKE ? OR LOWER(matched_id) LIKE ? OR LOWER(matched_category) LIKE ?)"
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    sql += " ORDER BY datetime DESC, id DESC"

    rows = _rows_as_dicts(conn.execute(sql, params))
    conn.close()
    return jsonify(rows)


EDITABLE_FIELDS = [
    "raw_name", "matched_id", "matched_category", "matched_subcategory",
    "quantity", "unit", "total_price", "source", "datetime", "gpt_notes",
]


def _coerce_float(val):
    try:
        return float(val) if val not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


@bp.patch("/entries/<int:entry_id>")
def update_entry(entry_id: int):
    data = request.get_json(force=True)
    conn = _db()
    row = conn.execute("SELECT quantity, total_price FROM purchases WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "not found"}), 404

    quantity = _coerce_float(data["quantity"]) if "quantity" in data else row[0]
    total_price = _coerce_float(data["total_price"]) if "total_price" in data else row[1]
    # unit_price is always derived, never trusted from the client — same
    # invariant as receipt parsing and chat-logged purchases.
    unit_price = round(total_price / quantity, 4) if quantity else None

    fields, values = [], []
    for f in EDITABLE_FIELDS:
        if f in data:
            fields.append(f"{f} = ?")
            values.append(data[f])
    fields.append("unit_price = ?")
    values.append(unit_price)
    values.append(entry_id)

    conn.execute(f"UPDATE purchases SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    updated = _rows_as_dicts(conn.execute(
        "SELECT id, datetime, raw_name, matched_id, matched_category, matched_subcategory, "
        "quantity, unit, unit_price, total_price, source, gpt_notes FROM purchases WHERE id = ?",
        (entry_id,),
    ))[0]
    conn.close()
    return jsonify(updated)


@bp.delete("/entries/<int:entry_id>")
def delete_entry(entry_id: int):
    conn = _db()
    cur = conn.execute("DELETE FROM purchases WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        return jsonify({"error": "not found"}), 404
    return jsonify({"deleted": entry_id})

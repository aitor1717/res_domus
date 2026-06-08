import csv
import io
from pathlib import Path
from flask import Blueprint, jsonify, request, current_app

bp = Blueprint("items", __name__, url_prefix="/api/items")

FIELDS = ["id", "item", "unit", "category", "subcategory", "synonyms", "notes", "tags"]


def _load() -> list[dict]:
    path = Path(current_app.config["AUX_CSV"])
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _save(rows: list[dict]) -> None:
    path = Path(current_app.config["AUX_CSV"])
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _next_id(rows: list[dict]) -> int:
    ids = [int(r.get("id") or 0) for r in rows if str(r.get("id") or "").isdigit()]
    return max(ids, default=0) + 1


@bp.get("")
def list_items():
    q = (request.args.get("q") or "").lower().strip()
    rows = _load()
    if q:
        rows = [
            r for r in rows
            if q in (r.get("item") or "").lower()
            or q in (r.get("synonyms") or "").lower()
            or q in (r.get("category") or "").lower()
        ]
    return jsonify(rows)


@bp.post("")
def create_item():
    data = request.get_json(force=True)
    rows = _load()
    new_row = {
        "id":          str(_next_id(rows)),
        "item":        (data.get("item") or "").strip(),
        "unit":        (data.get("unit") or "").strip(),
        "category":    (data.get("category") or "").strip(),
        "subcategory": (data.get("subcategory") or "").strip(),
        "synonyms":    (data.get("synonyms") or "").strip(),
        "notes":       (data.get("notes") or "").strip(),
        "tags":        (data.get("tags") or "").strip(),
    }
    if not new_row["item"]:
        return jsonify({"error": "item name required"}), 400
    rows.append(new_row)
    _save(rows)
    return jsonify(new_row), 201


@bp.patch("/<item_id>")
def update_item(item_id: str):
    data = request.get_json(force=True)
    rows = _load()
    for row in rows:
        if str(row.get("id")) == item_id:
            for field in FIELDS:
                if field in data and field != "id":
                    row[field] = data[field]
            _save(rows)
            return jsonify(row)
    return jsonify({"error": "not found"}), 404


@bp.delete("/<item_id>")
def delete_item(item_id: str):
    rows = _load()
    original = len(rows)
    rows = [r for r in rows if str(r.get("id")) != item_id]
    if len(rows) == original:
        return jsonify({"error": "not found"}), 404
    _save(rows)
    return jsonify({"deleted": item_id})

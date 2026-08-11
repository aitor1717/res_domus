"""
Grocery receipt parser — callable from Flask or CLI.
"""

import anthropic
import base64
import csv
import json
import re
import shutil
import sqlite3
import time
from datetime import datetime, date, timedelta
from pathlib import Path

from csv_safety import desanitize_cell, sanitize_cell
from parser.prompts import build_parser_system, build_parser_user

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MEDIA_TYPE_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
    ".gif": "image/gif",
}

CSV_FIELDS = [
    "raw_name", "matched_id", "matched_category", "matched_subcategory",
    "tags", "unit", "quantity", "unit_price", "total_price",
    "source", "order_id", "payment_method", "datetime", "gpt_notes",
]

MONTHS_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
    "jan": 1, "apr": 4, "aug": 8, "dec": 12,
}
MONTHS_ABR = {
    1: "jan", 2: "feb", 3: "mar", 4: "apr", 5: "may", 6: "jun",
    7: "jul", 8: "aug", 9: "sep", 10: "oct", 11: "nov", 12: "dec",
}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


# ── date helpers ──────────────────────────────────────────────────────────────

def try_parse_date_string(s: str) -> date | None:
    s = s.lower().replace("_", " ").replace("-", " ")
    m = re.search(r"(\d{1,2})\s+([a-z]{3})\s*(\d{4})?", s)
    if m:
        day = int(m.group(1))
        mon = MONTHS_ES.get(m.group(2)[:3])
        year = int(m.group(3)) if m.group(3) else date.today().year
        if mon:
            try:
                return date(year, mon, day)
            except ValueError:
                pass
    m = re.search(r"(\d{4})\D?(\d{2})\D?(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})\D(\d{1,2})(?:\D(\d{2,4}))?", s)
    if m:
        day, mon = int(m.group(1)), int(m.group(2))
        yr_raw = m.group(3)
        year = int(yr_raw) + (2000 if yr_raw and len(yr_raw) == 2 else 0) if yr_raw else date.today().year
        try:
            return date(year, mon, day)
        except ValueError:
            pass
    return None


def parse_user_date(raw: str) -> date | None:
    today = date.today()
    raw = raw.strip().lower()
    if raw in ("today", "hoy"):
        return today
    if raw in ("yesterday", "ayer"):
        return today - timedelta(days=1)
    m = re.match(r"last\s+(\w+)", raw)
    if m and m.group(1) in WEEKDAYS:
        target = WEEKDAYS.index(m.group(1))
        delta = (today.weekday() - target) % 7 or 7
        return today - timedelta(days=delta)
    m = re.match(r"(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?$", raw)
    if m:
        day, mon = int(m.group(1)), int(m.group(2))
        yr_raw = m.group(3)
        year = int(yr_raw) + (2000 if yr_raw and len(yr_raw) == 2 else 0) if yr_raw else date.today().year
        try:
            return date(year, mon, day)
        except ValueError:
            pass
    return None


def fmt_date(d: date) -> str:
    return f"{d.day:02d}_{MONTHS_ABR[d.month]}_{d.year}"


# ── canonical items ───────────────────────────────────────────────────────────

def load_canonical_items(csv_path: Path) -> list[dict]:
    items = []
    if not csv_path.exists():
        return items
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            # aux_items.csv is written with a formula-injection guard (see
            # csv_safety.py) - undo it here so a protective leading quote
            # never reaches Claude's item-matching prompt.
            row = {k: desanitize_cell(v) for k, v in row.items()}
            synonyms_raw = row.get("synonyms", "") or ""
            synonyms = [s.strip() for s in synonyms_raw.replace(";", ",").split(",") if s.strip()]
            name = (row.get("item") or row.get("id") or "").strip()
            items.append({
                "id":          name,
                "category":    row.get("category", "").strip(),
                "subcategory": row.get("subcategory", "").strip(),
                "tags":        row.get("tags", "").strip(),
                "unit":        row.get("unit", "").strip(),
                "synonyms":    synonyms,
            })
    return items


# ── image helpers ─────────────────────────────────────────────────────────────

def encode_image(path: Path) -> tuple[str, str]:
    ext = path.suffix.lower()
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode(), MEDIA_TYPE_MAP.get(ext, "image/jpeg")


def collect_image_groups(input_dir: Path) -> list[tuple[str, list[Path]]]:
    groups = []
    for sub in sorted(input_dir.iterdir()):
        if sub.is_dir():
            imgs = sorted(p for p in sub.iterdir() if p.suffix.lower() in IMAGE_EXTS)
            if imgs:
                groups.append((sub.name, imgs))
    loose = sorted(p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    if loose:
        groups.append((f"order_{datetime.now().strftime('%Y%m%d_%H%M%S')}", loose))
    return groups


def infer_date_from_group(group_name: str, images: list[Path]) -> date | None:
    d = try_parse_date_string(group_name)
    if d:
        return d
    for img in images:
        d = try_parse_date_string(img.stem)
        if d:
            return d
    return None


# ── API call ──────────────────────────────────────────────────────────────────

def parse_group(
    client: anthropic.Anthropic,
    images: list[Path],
    system_prompt: str,
    user_text: str,
    retries: int = 2,
    model: str = "claude-sonnet-4-6",
) -> list[dict]:
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": mt, "data": d}}
        for d, mt in (encode_image(img) for img in images)
    ] + [{"type": "text", "text": user_text}]
    messages = [{"role": "user", "content": content}]

    last_err = None
    for attempt in range(1 + retries):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=4096,
                system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                if raw.endswith("```"):
                    raw = raw.rsplit("```", 1)[0]
            return json.loads(raw)
        except (anthropic.APIError, json.JSONDecodeError) as e:
            last_err = e
            if attempt < retries:
                if isinstance(e, json.JSONDecodeError):
                    # Feed the malformed response and the parse error back so Claude
                    # can self-correct, instead of blindly resending the same request.
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({
                        "role": "user",
                        "content": f"That wasn't valid JSON ({e}). Return only the corrected JSON array, no other text.",
                    })
                time.sleep(3 * (attempt + 1))
    raise last_err


def clean_items(items: list[dict], order_date_str: str) -> list[dict]:
    """Apply confirmed date and strip residual date notes from gpt_notes."""
    for item in items:
        item["datetime"] = order_date_str
        if item.get("gpt_notes"):
            item["gpt_notes"] = re.sub(
                r";?\s*datetime not found", "", item["gpt_notes"]
            ).strip("; ")
    return items


# ── price outlier detection ───────────────────────────────────────────────────

def load_price_stats(db_path: Path) -> dict[str, tuple[float, float]]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    cur = conn.execute("""
        SELECT matched_id,
               AVG(unit_price) AS mean,
               SQRT(AVG(unit_price*unit_price) - AVG(unit_price)*AVG(unit_price)) AS std
        FROM purchases
        WHERE unit_price IS NOT NULL AND matched_id IS NOT NULL
        GROUP BY matched_id
        HAVING COUNT(*) >= 3
    """)
    stats = {row[0]: (row[1], row[2] or 0.0) for row in cur.fetchall()}
    conn.close()
    return stats


def flag_outliers(items: list[dict], stats: dict[str, tuple[float, float]]) -> None:
    for item in items:
        mid = item.get("matched_id")
        up = item.get("unit_price")
        if not mid or up is None or mid not in stats:
            continue
        mean, std = stats[mid]
        if std == 0:
            continue
        z = (float(up) - mean) / std
        if abs(z) > 3:
            note = f"price outlier: {up:.3f} vs mean {mean:.3f} (z={z:.1f}). Verify"
            existing = item.get("gpt_notes", "")
            item["gpt_notes"] = f"{existing}; {note}".strip("; ")


# ── output helpers ────────────────────────────────────────────────────────────

def _make_total_row(items: list[dict]) -> dict:
    total = round(sum(float(r.get("total_price") or 0) for r in items), 4)
    base = items[0] if items else {}
    return {
        "raw_name": "TOTAL", "matched_id": None, "matched_category": None,
        "matched_subcategory": None, "tags": None, "unit": None,
        "quantity": None, "unit_price": None, "total_price": total,
        "source": base.get("source"), "order_id": base.get("order_id"),
        "payment_method": base.get("payment_method"),
        "datetime": base.get("datetime"),
        "gpt_notes": f"sum of {len(items)} items",
    }


def save_review(
    group_name: str,
    order_date: date,
    items: list[dict],
    images: list[Path],
    review_dir: Path,
) -> Path:
    review_dir.mkdir(parents=True, exist_ok=True)
    date_str = fmt_date(order_date)
    stem = f"{date_str}_{group_name}"

    csv_path = review_dir / f"{stem}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        # Formula-injection guard (see csv_safety.py): raw_name/source/etc.
        # can carry Claude's OCR read of a photographed receipt, not
        # trusted internal data. Sanitized on copies, not `items` itself -
        # the caller's list is still used elsewhere (dedup, the frontend
        # review table) and must keep the unmodified values.
        rows = [
            {k: sanitize_cell(v) for k, v in row.items()}
            for row in items + [_make_total_row(items)]
        ]
        writer.writerows(rows)

    for i, img in enumerate(images, 1):
        shutil.copy2(img, review_dir / f"{date_str}_img_{i:02d}{img.suffix.lower()}")

    return csv_path


def archive_images(images: list[Path], group_name: str, archive_dir: Path, input_dir: Path) -> None:
    dest = archive_dir / group_name
    dest.mkdir(parents=True, exist_ok=True)
    for img in images:
        shutil.move(str(img), dest / img.name)
    parent = images[0].parent
    if parent != input_dir and not any(parent.iterdir()):
        parent.rmdir()


# ── CLI entry point ───────────────────────────────────────────────────────────

def _cli_main():
    import sys
    from pathlib import Path as P

    try:
        import config as cfg
    except ImportError:
        print("config.py not found — copy config.example.py and fill in your API key.")
        sys.exit(1)

    cfg.ensure_dirs()

    canonical_items = load_canonical_items(cfg.AUX_CSV)
    print(f"Loaded {len(canonical_items)} canonical items")

    price_stats = load_price_stats(cfg.DB_PATH)
    print(f"Loaded price stats for {len(price_stats)} items")

    client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
    groups = collect_image_groups(cfg.UPLOAD_DIR)
    if not groups:
        print("No images found in input/")
        return

    for group_name, images in groups:
        print(f"\nProcessing '{group_name}' — {len(images)} image(s)")
        inferred = infer_date_from_group(group_name, images)

        if inferred:
            print(f"Inferred date: {inferred}. Press Enter to confirm or type a new date:")
        else:
            print("No date found. Enter purchase date (DD/MM, 'yesterday', 'last thursday'):")

        raw = input("> ").strip()
        order_date = (parse_user_date(raw) or inferred) if raw else inferred
        if not order_date:
            print("Could not determine date, skipping.")
            continue

        system_prompt = build_parser_system(canonical_items)
        user_text = build_parser_user(order_date.isoformat())
        try:
            items = parse_group(client, images, system_prompt, user_text, model=getattr(cfg, "MODEL_PARSER", "claude-sonnet-4-6"))
        except Exception as e:
            print(f"Parse failed: {e}")
            continue

        items = clean_items(items, order_date.isoformat())
        flag_outliers(items, price_stats)

        csv_path = save_review(group_name, order_date, items, images, cfg.REVIEW_DIR)
        archive_images(images, group_name, cfg.ARCHIVE_DIR, cfg.UPLOAD_DIR)
        print(f"{len(items)} items → {csv_path.name}")


if __name__ == "__main__":
    _cli_main()

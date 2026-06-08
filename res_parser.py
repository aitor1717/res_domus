"""
Grocery Parser ⋆｡𖦹° ⋆ ｡ 𖦹 °⭒ ˚｡ ⋆ °  𖦹    ⋆ 
"""

import anthropic
import base64
import csv
import json
import random
import re
import shutil
import sqlite3
import time
from datetime import datetime, date, timedelta
from pathlib import Path

from config import ANTHROPIC_API_KEY

BASE_DIR    = Path(__file__).resolve().parent
AUX_CSV     = BASE_DIR / "aux_items.csv"
INPUT_DIR   = BASE_DIR / "input"
REVIEW_DIR  = BASE_DIR / "review"
ARCHIVE_DIR = BASE_DIR / "archive"
DB_PATH     = BASE_DIR / "res_domus.db"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MEDIA_TYPE_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png",  ".webp": "image/webp",
    ".gif": "image/gif",
}

CSV_FIELDS = [
    "raw_name", "matched_id", "matched_category", "matched_subcategory",
    "tags", "unit", "quantity", "unit_price", "total_price",
    "source", "order_id", "payment_method", "datetime", "gpt_notes",
]

MONTHS_ES = {
    "ene":1,"feb":2,"mar":3,"abr":4,"may":5,"jun":6,
    "jul":7,"ago":8,"sep":9,"oct":10,"nov":11,"dic":12,
    "jan":1,"apr":4,"aug":8,"dec":12,
}
MONTHS_ABR = {
    1:"jan",2:"feb",3:"mar",4:"apr",5:"may",6:"jun",
    7:"jul",8:"aug",9:"sep",10:"oct",11:"nov",12:"dec",
}
WEEKDAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]


# ── waveform print ────────────────────────────────────────────────────────────

def wave() -> str:
    chars = " ★ "
    length = random.randint(5, 20)
    body = "".join(random.choice(chars) for _ in range(length))
    return f" {body} "

def p(msg: str) -> None:
    print(f"\n{msg}{wave()}")


# ── date helpers ──────────────────────────────────────────────────────────────

def try_parse_date_string(s: str) -> date | None:
    """Try to extract a date from a freeform string (folder/file name)."""
    s = s.lower().replace("_", " ").replace("-", " ")

    # DD mon YYYY or DD mon
    m = re.search(r"(\d{1,2})\s+([a-z]{3})\s*(\d{4})?", s)
    if m:
        day = int(m.group(1))
        mon = MONTHS_ES.get(m.group(2)[:3])
        year = int(m.group(3)) if m.group(3) else date.today().year
        if mon:
            try: return date(year, mon, day)
            except ValueError: pass

    # YYYY MM DD or YYYYMMDD
    m = re.search(r"(\d{4})\D?(\d{2})\D?(\d{2})", s)
    if m:
        try: return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError: pass

    # DD MM (YYYY)?
    m = re.search(r"(\d{1,2})\D(\d{1,2})(?:\D(\d{2,4}))?", s)
    if m:
        day, mon = int(m.group(1)), int(m.group(2))
        yr_raw = m.group(3)
        year = int(yr_raw) + (2000 if yr_raw and len(yr_raw) == 2 else 0) if yr_raw else date.today().year
        try: return date(year, mon, day)
        except ValueError: pass

    return None


def parse_user_date(raw: str) -> date | None:
    today = date.today()
    raw = raw.strip().lower()
    if raw in ("today", "hoy"): return today
    if raw in ("yesterday", "ayer"): return today - timedelta(days=1)
    m = re.match(r"last\s+(\w+)", raw)
    if m and m.group(1) in WEEKDAYS:
        target = WEEKDAYS.index(m.group(1))
        delta = (today.weekday() - target) % 7 or 7
        return today - timedelta(days=delta)
    # DD/MM or DD/MM/YY(YY)
    m = re.match(r"(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?$", raw)
    if m:
        day, mon = int(m.group(1)), int(m.group(2))
        yr_raw = m.group(3)
        year = int(yr_raw) + (2000 if yr_raw and len(yr_raw)==2 else 0) if yr_raw else date.today().year
        try: return date(year, mon, day)
        except ValueError: pass
    return None


def confirm_date(inferred: date | None, hint: str) -> date:
    today = date.today()
    if inferred:
        p(f"Date inferred from '{hint}': {inferred.strftime('%d %b %Y')}. Confirm? [Enter] or type a new date")
        raw = input("\n⋆｡𖦹° ⋆ ｡ 𖦹 °⭒ ˚｡ ⋆ °  𖦹    ⋆ ").strip()
        if not raw:
            return inferred
        resolved = parse_user_date(raw)
        if resolved:
            return resolved
        p("Could not parse. Using inferred date.")
        return inferred
    else:
        p(f"No date found in '{hint}'. Enter purchase date (DD-MM, 'yesterday', 'last thursday')")
        while True:
            raw = input("\n⋆｡𖦹° ⋆ ｡ 𖦹 °⭒ ˚｡ ⋆ °  𖦹    ⋆ ").strip()
            resolved = parse_user_date(raw)
            if resolved:
                return resolved
            p("Could not parse. Try DD/MM, DD-MM-YYYY, 'yesterday', 'last <weekday>'")


def fmt_date(d: date) -> str:
    return f"{d.day:02d}_{MONTHS_ABR[d.month]}_{d.year}"


# ── canonical items ───────────────────────────────────────────────────────────

def load_canonical_items(csv_path: Path) -> list[dict]:
    items = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
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


# ── prompt ────────────────────────────────────────────────────────────────────

def build_prompt(canonical_items: list[dict], order_date: str) -> str:
    items_json = json.dumps(canonical_items, ensure_ascii=False)
    return f"""You are a grocery receipt parser. Extract every line item and return a valid JSON array.

## INPUT TYPES
App cart screenshots (PedidosYa, Tottus, etc.), handwritten market receipts, Notes app screenshots, date-overlaid screenshots. Parse all the same way.

## CANONICAL ITEM LIST
Match each item by name and synonyms. No match → matched_id, matched_category, matched_subcategory, tags all null.

{items_json}

## RULES
- unit: always use the canonical unit from the list; use sold unit if no match. Parse handwritten text (like panadero, vegetable names or balon de gas)
- quantity: convert to canonical scale (500g→0.5 if canonical=kg; 900ml→0.9 if canonical=l; 180g stays 180 if canonical=g). Incompatible dimensions → flag as "unit mismatch: X→Y".
- packs: expand to individual units (30 Unidades → quantity=30); flag only if ambiguous.
- unit_price: always total_price / quantity. Ignore promotional labels entirely — do not use them to compute prices.
- total_price: the amount paid as shown. For any bundle/promo deal, use the displayed total for that line as-is.
- source: full name as shown (e.g. "PedidosYa Market - San Borja", "Tottus", "Mercado", or "Desconocido").
- order_id, payment_method: if visible, else null.
- delivery/service fees: parse as regular line items.
- datetime: use exactly "{order_date}".
- matched_subcategory: from canonical list if matched, else null.
- tags: from canonical list if matched, else null.
- gpt_notes: SHORT flags only — unresolvable mismatch, unidentifiable item. Format: "issue → proposed fix". Empty string if clean.

## OUTPUT
JSON array only, no prose:
[{{"raw_name":"...","matched_id":"...","matched_category":"...","matched_subcategory":"...","tags":"...","unit":"...","quantity":0.0,"unit_price":0.0,"total_price":0.0,"source":"...","order_id":null,"payment_method":null,"datetime":"{order_date}","gpt_notes":""}}]"""


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
    if d: return d
    for img in images:
        d = try_parse_date_string(img.stem)
        if d: return d
    return None


# ── API call ──────────────────────────────────────────────────────────────────

def parse_group(client: anthropic.Anthropic, images: list[Path], prompt: str, retries: int = 2) -> list[dict]:
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": mt, "data": d}}
        for d, mt in (encode_image(img) for img in images)
    ] + [{"type": "text", "text": prompt}]

    last_err = None
    for attempt in range(1 + retries):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=4096,
                messages=[{"role": "user", "content": content}],
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
                wait = 3 * (attempt + 1)
                p(f"Attempt {attempt+1} failed ({e}), retrying in {wait}s")
                time.sleep(wait)
    raise last_err


# ── SD outlier check ──────────────────────────────────────────────────────────

def load_price_stats(db_path: Path) -> dict[str, tuple[float, float]]:
    """Returns {matched_id: (mean_unit_price, std_unit_price)}."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    cur = conn.execute("""
        SELECT matched_id,
               AVG(unit_price)                                        AS mean,
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
        up  = item.get("unit_price")
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


# ── total row ─────────────────────────────────────────────────────────────────

def make_total_row(items: list[dict]) -> dict:
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


# ── output ────────────────────────────────────────────────────────────────────

def save_review(group_name: str, order_date: date, items: list[dict], images: list[Path]) -> Path:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    date_str = fmt_date(order_date)
    stem = f"{date_str}_{group_name}"

    csv_path = REVIEW_DIR / f"{stem}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(items + [make_total_row(items)])

    for i, img in enumerate(images, 1):
        shutil.copy2(img, REVIEW_DIR / f"{date_str}_img_{i:02d}{img.suffix.lower()}")

    return csv_path


def archive_images(images: list[Path], group_name: str) -> None:
    dest = ARCHIVE_DIR / group_name
    dest.mkdir(parents=True, exist_ok=True)
    for img in images:
        shutil.move(str(img), dest / img.name)
    parent = images[0].parent
    if parent != INPUT_DIR and not any(parent.iterdir()):
        parent.rmdir()


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    for d in (INPUT_DIR, REVIEW_DIR, ARCHIVE_DIR):
        d.mkdir(parents=True, exist_ok=True)

    if not AUX_CSV.exists():
        raise FileNotFoundError(f"Aux CSV not found: {AUX_CSV}")

    canonical_items = load_canonical_items(AUX_CSV)
    p(f"Loaded {len(canonical_items)} canonical items")

    price_stats = load_price_stats(DB_PATH)
    p(f"Loaded price stats for {len(price_stats)} items from DB")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    groups = collect_image_groups(INPUT_DIR)
    if not groups:
        p("No images found in input")
        return

    p(f"Found {len(groups)} group(s)")

    for group_name, images in groups:
        p(f"Processing '{group_name}' — {len(images)} image(s)")

        inferred = infer_date_from_group(group_name, images)
        order_date = confirm_date(inferred, group_name)
        order_date_str = order_date.isoformat()

        prompt = build_prompt(canonical_items, order_date_str)

        try:
            items = parse_group(client, images, prompt)
        except Exception as e:
            p(f"Failed: {e}")
            continue

        # Apply confirmed date to all items; strip residual datetime notes
        for item in items:
            item["datetime"] = order_date_str
            if item.get("gpt_notes"):
                item["gpt_notes"] = re.sub(r";?\s*datetime not found", "", item["gpt_notes"]).strip("; ")

        flag_outliers(items, price_stats)

        flagged = [r for r in items if r.get("gpt_notes")]
        if flagged:
            p(f"{len(flagged)} flag(s):")

        csv_path = save_review(group_name, order_date, items, images)
        archive_images(images, group_name)
        p(f"{len(items)} items added to {csv_path.name}")


if __name__ == "__main__":
    main()
    print('\n')

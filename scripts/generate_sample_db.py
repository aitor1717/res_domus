#!/usr/bin/env python3
"""
Generate a synthetic purchases database from a sample data template CSV.

Usage (from repo root):
    python scripts/generate_sample_db.py                          # Spanish demo
    python scripts/generate_sample_db.py --lang en                # English demo
    python scripts/generate_sample_db.py --template my.csv --db data/test.db
"""

import argparse
import calendar
import csv
import math
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from parser.build_db import SCHEMA, VIEWS

PRICE_NOISE = 0.12   # ±12% price jitter per purchase
QTY_NOISE   = 0.18   # ±18% quantity jitter per purchase
INFLATION   = 0.009  # ~1% monthly price drift (upward)

# Seasonal spend multiplier by month (1=Jan … 12=Dec)
# Holidays in Nov/Dec drive spend up; Jan/Feb quiet; summer moderate peak
SEASONAL = {1:0.88, 2:0.85, 3:0.95, 4:1.00, 5:1.05, 6:1.08,
            7:1.06, 8:1.04, 9:0.98, 10:1.02, 11:1.12, 12:1.18}

INSERT_SQL = """
INSERT INTO purchases (
    raw_name, matched_id, matched_category, matched_subcategory, tags,
    unit, quantity, unit_price, total_price,
    source, order_id, payment_method, datetime, gpt_notes, source_file
) VALUES (
    :raw_name, :matched_id, :matched_category, :matched_subcategory, :tags,
    :unit, :quantity, :unit_price, :total_price,
    :source, :order_id, :payment_method, :datetime, :gpt_notes, :source_file
)
"""


def poisson(lam: float, rng: random.Random) -> int:
    if lam <= 0:
        return 0
    L = math.exp(-lam)
    k, p = 0, 1.0
    while p > L:
        k += 1
        p *= rng.random()
    return k - 1


def iter_months(months_back: int):
    today = date.today()
    sm = today.month - (months_back % 12)
    sy = today.year - (months_back // 12)
    if sm <= 0:
        sm += 12
        sy -= 1
    y, m = sy, sm
    while (y, m) <= (today.year, today.month):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def make_row(tmpl: dict, aux_row: dict, dt: date, unit_price: float, quantity: float) -> dict:
    return {
        "raw_name":            tmpl["item"],
        "matched_id":          tmpl["item"],   # item NAME, not numeric id
        "matched_category":    tmpl["category"],
        "matched_subcategory": aux_row.get("subcategory", ""),
        "tags":                aux_row.get("tags", ""),
        "unit":                tmpl["unit"],
        "quantity":            round(quantity, 3),
        "unit_price":          round(unit_price, 4),
        "total_price":         round(unit_price * quantity, 2),
        "source":              tmpl["store"],
        "order_id":            None,
        "payment_method":      None,
        "datetime":            dt.isoformat(),
        "gpt_notes":           None,
        "source_file":         "sample-data",
    }


def generate(template_path: Path, aux_path: Path, months: int, rng: random.Random) -> list[dict]:
    with open(aux_path, newline="", encoding="utf-8") as f:
        aux = {r["id"]: r for r in csv.DictReader(f)}

    with open(template_path, newline="", encoding="utf-8") as f:
        template = [r for r in csv.DictReader(f) if float(r.get("purchases_per_month", 0)) > 0]

    price_col = next((c for c in ("unit_price_soles", "unit_price_usd", "unit_price") if c in (template[0] if template else {})), None)
    if not price_col:
        sys.exit("Template missing price column (unit_price_soles / unit_price_usd)")

    all_months = list(iter_months(months))
    total = len(all_months)
    today = date.today()
    rows = []

    for tmpl in template:
        aux_row    = aux.get(tmpl["id"], {})
        base_price = float(tmpl[price_col])
        base_qty   = float(tmpl["qty_per_purchase"])
        freq       = float(tmpl["purchases_per_month"])

        import datetime as _dt
        for i, (y, m) in enumerate(all_months):
            _, days_in = calendar.monthrange(y, m)
            is_current = (y == today.year and m == today.month)
            # Scale expected purchases for the current month by days elapsed
            effective_freq = freq * (today.day / days_in) if is_current else freq
            n = poisson(effective_freq, rng)
            if n == 0:
                continue
            day_range = today.day if is_current else days_in
            months_ago   = total - 1 - i
            price_factor = (1 + INFLATION) ** (-months_ago) * SEASONAL[m]
            for _ in range(n):
                day = rng.randint(1, day_range)
                dt  = date(y, m, day)
                unit_price = base_price * price_factor * (1 + rng.uniform(-PRICE_NOISE, PRICE_NOISE))
                quantity   = base_qty   * SEASONAL[m] * (1 + rng.uniform(-QTY_NOISE, QTY_NOISE))
                rows.append(make_row(tmpl, aux_row, dt, unit_price, quantity))

    rows.sort(key=lambda r: r["datetime"])
    return rows


def _balance_after_write(conn: sqlite3.Connection, rng: random.Random) -> None:
    """
    After all rows are written, query v_item_stats and add synthetic
    recent-purchase rows to produce a deliberate urgency gradient in
    the Running Low section:

      pos 0-1   → urgency 1.12, 1.08  (red, slightly overdue)
      pos 2-8   → urgency 0.80-0.38   (orange / yellow)  [bucket 2 in stratified query]
      pos 9-11  → urgency 0.22        (green)             [bucket 3]
      rank 13+  → urgency 0.22        (green, below bucket 2 threshold)

    All overdue items not in the top 12 are also caught up so they don't
    displace the targeted items in the stratified query.
    """
    today = date.today()
    # Targets for positions 0-11 in the final display
    TARGETS = [1.12, 1.08, 0.80, 0.72, 0.64, 0.56, 0.48, 0.42, 0.38, 0.15, 0.15, 0.15]
    CATCHUP = 0.15   # urgency target for all remaining overdue items (rank 13+)
    SKIP_CATS = "('Delivery', 'Courier', 'Servicio', 'Service')"

    all_overdue = conn.execute(f"""
        SELECT matched_id, reorder_urgency, avg_interval_days
        FROM v_item_stats
        WHERE is_reliable = 1 AND reorder_urgency IS NOT NULL
          AND matched_category NOT IN {SKIP_CATS}
        ORDER BY reorder_urgency DESC
    """).fetchall()

    col_names = [r[1] for r in conn.execute("PRAGMA table_info(purchases)").fetchall()]

    def insert_catchup(mid: str, urgency: float, avg_int: float, target: float) -> None:
        if urgency is None or avg_int is None or urgency <= target:
            return
        target_days_ago = max(1, round(target * avg_int))
        new_last = today - timedelta(days=target_days_ago)
        ref_row = conn.execute(
            "SELECT * FROM purchases WHERE matched_id = ? ORDER BY datetime DESC LIMIT 1",
            (mid,)
        ).fetchone()
        if not ref_row:
            return
        ref = dict(zip(col_names, ref_row))
        ref.pop("id", None)
        ref["datetime"] = new_last.isoformat()
        ref["unit_price"] = round(ref["unit_price"] * (1 + rng.uniform(-0.04, 0.04)), 4)
        if new_last.month == today.month:
            # Zero quantity/total only for current-month rows to avoid inflating
            # the budget ring. Prior-month rows keep real prices so the chart
            # doesn't look artificially sparse in recent weeks.
            ref["quantity"]    = 0
            ref["total_price"] = 0
        else:
            ref["total_price"] = round(ref["unit_price"] * ref["quantity"], 2)
        conn.execute(INSERT_SQL, ref)

    for i, (mid, urgency, avg_int) in enumerate(all_overdue):
        target = TARGETS[i] if i < len(TARGETS) else CATCHUP
        insert_catchup(mid, urgency, avg_int, target)

    conn.commit()
    conn.executescript(VIEWS)


def _shape_demo(conn: sqlite3.Connection, rng: random.Random) -> None:
    """
    After the balance pass, add two demo-specific adjustments:

    1. Spending spike — inserts ~$200 of realistic purchases on June 23 and June 25
       (17–15 days before July 10), turning week 25 (June 22–28) into a visible
       but not dramatic peak just before the last two chart entries.

    2. Manual budget — sets July's budget to spent_this_month / 1.06, so the
       budget ring shows ~6% over, telling the story of a household that set
       a tight July goal and has already exceeded it.
    """
    today = date.today()
    SKIP_CATS = "('Delivery', 'Courier', 'Servicio', 'Service')"
    col_names = [r[1] for r in conn.execute("PRAGMA table_info(purchases)").fetchall()]

    # Pull a pool of reliable items with their typical price/qty.
    # Exclude quantity=0 rows (balance phantoms) so avg_qty stays realistic.
    # Exclude Toilet Paper — handled separately below so days_of_stock_left is exact.
    pool = conn.execute(f"""
        SELECT p.matched_id, p.matched_category, p.matched_subcategory,
               p.tags, p.unit, p.source, p.payment_method, p.gpt_notes,
               AVG(p.unit_price) AS avg_price, AVG(p.quantity) AS avg_qty
        FROM purchases p
        JOIN v_item_stats v ON v.matched_id = p.matched_id
        WHERE v.is_reliable = 1
          AND p.matched_category NOT IN {SKIP_CATS}
          AND p.total_price > 0
          AND p.quantity > 0
          AND p.matched_id != 'Toilet Paper'
        GROUP BY p.matched_id
        ORDER BY AVG(p.total_price) DESC
        LIMIT 40
    """).fetchall()

    def _add_trip(trip_date: date, target: float, items: list) -> None:
        rng.shuffle(items)
        spent = 0.0
        for row in items:
            if spent >= target:
                break
            mid, cat, subcat, tags, unit, source, payment, notes, avg_p, avg_q = row
            price = avg_p * (1 + rng.uniform(-0.06, 0.06))
            qty   = avg_q * (1 + rng.uniform(-0.08, 0.08))
            total = round(price * qty, 2)
            conn.execute(INSERT_SQL, {
                "raw_name":            mid,
                "matched_id":          mid,
                "matched_category":    cat,
                "matched_subcategory": subcat,
                "tags":                tags,
                "unit":                unit,
                "quantity":            round(qty, 3),
                "unit_price":          round(price, 4),
                "total_price":         total,
                "source":              source or "Supermarket",
                "order_id":            None,
                "payment_method":      payment,
                "datetime":            trip_date.isoformat(),
                "gpt_notes":           notes,
                "source_file":         "sample-data",
            })
            spent += total

    if pool:
        items = list(pool)
        # Regular shops through the 30d window (adds density/realism)
        _add_trip(today - timedelta(days=28),  55, items)  # June 12
        _add_trip(today - timedelta(days=21),  65, items)  # June 19
        # Spike: two big shops on June 23 and June 25 (W25, visible bump before last two entries)
        _add_trip(today - timedelta(days=17), 110, items)  # June 23
        _add_trip(today - timedelta(days=15), 100, items)  # June 25
        _add_trip(today - timedelta(days=14),  60, items)  # June 26
        # Filler: bring W26 (June 29–July 5) and W27 (July 6–today) to realistic levels
        _add_trip(today - timedelta(days=9),   80, items)  # July 1
        _add_trip(today - timedelta(days=6),   50, items)  # July 4
        _add_trip(today - timedelta(days=3),   70, items)  # July 7
        conn.commit()
        conn.executescript(VIEWS)

    # Engineer toilet paper to show exactly ~2 days of stock remaining.
    # Iterate: read v_item_stats → back-solve the needed days_since → reinsert → repeat.
    tp_ref = conn.execute(
        "SELECT * FROM purchases WHERE matched_id='Toilet Paper' ORDER BY datetime LIMIT 1"
    ).fetchone()
    if tp_ref:
        TARGET_TP_DAYS = 2
        LAST_QTY       = 6.0
        tp_template    = dict(zip(col_names, tp_ref))
        tp_template.pop("id", None)
        tp_template["quantity"]    = LAST_QTY
        tp_template["unit_price"]  = round(tp_template.get("unit_price") or 3.0, 4)
        tp_template["total_price"] = round(tp_template["unit_price"] * LAST_QTY, 2)
        tp_template["source_file"] = "sample-data"

        for _ in range(4):
            stats = conn.execute(
                "SELECT daily_consumption, est_stock_remaining "
                "FROM v_item_stats WHERE matched_id='Toilet Paper'"
            ).fetchone()
            if not stats or not stats[0]:
                break
            daily_cons, est_rem = stats
            current_days_left = est_rem / daily_cons if daily_cons > 0 else 99
            if abs(current_days_left - TARGET_TP_DAYS) < 0.55:
                break
            days_since_need = max(1, round((LAST_QTY - TARGET_TP_DAYS * daily_cons) / daily_cons))
            tp_date = today - timedelta(days=days_since_need)
            conn.execute(
                "DELETE FROM purchases WHERE matched_id='Toilet Paper' AND datetime > ?",
                (tp_date.isoformat(),)
            )
            tp_template["datetime"] = tp_date.isoformat()
            conn.execute(INSERT_SQL, tp_template)
            conn.commit()
            conn.executescript(VIEWS)

    # Set manual budget so ring reads ~74% used (on-track but slightly over pace)
    row = conn.execute("SELECT spent_this_month FROM v_budget").fetchone()
    if row and row[0]:
        manual_budget = round(row[0] / 0.74, 2)
        cur_month = today.strftime("%Y-%m")
        conn.execute("DELETE FROM budget WHERE month = ?", (cur_month,))
        conn.execute("INSERT INTO budget (month, manual_budget) VALUES (?, ?)",
                     (cur_month, manual_budget))
        conn.commit()
        conn.executescript(VIEWS)


def write_db(db_path: Path, rows: list[dict], rng: random.Random | None = None) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)  # always start fresh
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.executescript(VIEWS)
    for row in rows:
        conn.execute(INSERT_SQL, row)
    conn.commit()
    _rng = rng or random.Random(99)
    _balance_after_write(conn, _rng)
    _shape_demo(conn, _rng)
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang",     choices=["es", "en"], default="es",
                    help="es = Spanish template + aux_items.csv, en = English versions")
    ap.add_argument("--template", help="Override template CSV path")
    ap.add_argument("--aux-items",help="Override aux_items CSV path")
    ap.add_argument("--db",       help="Output DB path")
    ap.add_argument("--months",   type=int, default=14)
    ap.add_argument("--seed",     type=int, default=42)
    args = ap.parse_args()

    if args.lang == "en":
        default_template  = ROOT / "sample_data_template_en.csv"
        default_aux       = ROOT / "app/aux_items_en.csv"
        default_db        = ROOT / "data/res_domus_demo_en.db"
    else:
        default_template  = ROOT / "sample_data_template.csv"
        default_aux       = ROOT / "app/aux_items.csv"
        default_db        = ROOT / "data/res_domus_demo.db"

    template_path = Path(args.template) if args.template else default_template
    aux_path      = Path(args.aux_items) if args.aux_items else default_aux
    db_path       = Path(args.db)       if args.db       else default_db

    rng  = random.Random(args.seed)
    rows = generate(template_path, aux_path, args.months, rng)
    write_db(db_path, rows, rng)

    cats = {}
    for r in rows:
        cats[r["matched_category"]] = cats.get(r["matched_category"], 0) + 1

    print(f"Generated {len(rows)} purchase rows → {db_path}")
    print(f"Months of history: {args.months}")
    print("By category:")
    for cat, cnt in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {cat:25s} {cnt:4d} purchases")


if __name__ == "__main__":
    main()

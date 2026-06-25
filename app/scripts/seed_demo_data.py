#!/usr/bin/env python3
"""
Generates ~14 months of synthetic grocery purchases and builds a demo
database at res_domus_demo.db — so the dashboard, price history, anomaly
detection, and stock estimates have realistic data to show off without
any real receipts or an Anthropic API key.

Usage:
    python scripts/seed_demo_data.py
    cp res_domus_demo.db res_domus.db   # on a fresh clone, to try the demo
"""

import csv
import random
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from parser.build_db import run_import  # noqa: E402

random.seed(42)

DATA_DIR = BASE_DIR.parent / "data"
DB_PATH = DATA_DIR / "res_domus_demo.db"
AUX_CSV = BASE_DIR / "aux_items.csv"

PRICE_RANGES = {
    "Frutas y Verduras": (1.5, 7),
    "Carnes":            (8, 28),
    "Abarrotes":         (2, 14),
    "Papá":              (6, 32),
    "Limpieza":          (3, 22),
    "Servicio":          (15, 40),
}

QTY_BY_UNIT = {
    "kg":        lambda: round(random.choice([0.5, 1, 1, 1.5, 2]), 2),
    "g":         lambda: 1,
    "l":         lambda: random.choice([1, 1, 2]),
    "u":         lambda: random.randint(1, 4),
    "latas":     lambda: random.randint(1, 6),
    "rollos":    lambda: random.choice([4, 8, 12]),
    "paquetes":  lambda: random.randint(1, 3),
    "sobres":    lambda: random.randint(1, 3),
}

SOURCES = ["Tottus", "Plaza Vea", "Wong", "Vivanda", "Metro", "Mercado"]
PAYMENT_METHODS = ["Tarjeta", "Efectivo"]

FIELDNAMES = [
    "raw_name", "matched_id", "matched_category", "matched_subcategory", "tags",
    "unit", "quantity", "unit_price", "total_price",
    "source", "order_id", "payment_method", "datetime", "gpt_notes",
]


def load_items():
    with open(AUX_CSV, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["category"] in PRICE_RANGES]

    items = []
    for r in rows:
        rng = random.Random(int(r["id"]))  # stable per-item base price
        lo, hi = PRICE_RANGES[r["category"]]
        base_price = round(rng.uniform(lo, hi), 2)
        items.append({**r, "base_price": base_price})
    return items


def build_trips(items):
    """Weekly-ish trips over ~14 months; staples appear more often than specialty items."""
    staples = [i for i in items if i["category"] in ("Frutas y Verduras", "Abarrotes")]
    others = [i for i in items if i["category"] not in ("Frutas y Verduras", "Abarrotes")]

    trips = []
    d = date.today() - timedelta(days=14 * 30)
    end = date.today()
    trip_idx = 0
    items_by_id = {i["id"]: i for i in items}
    # one-off price spikes on fixed trips, so v_anomalies has examples to show
    anomaly_schedule = {10: "27", 25: "19", 40: "76"}  # trip_idx -> matched_id

    while d <= end:
        n_staples = random.randint(6, 10)
        n_others = random.randint(1, 4)
        basket = random.sample(staples, min(n_staples, len(staples))) + \
            random.sample(others, min(n_others, len(others)))

        anomaly_id = anomaly_schedule.get(trip_idx)
        if anomaly_id and not any(it["id"] == anomaly_id for it in basket):
            basket.append(items_by_id[anomaly_id])

        source = random.choice(SOURCES)
        order_id = f"demo-{trip_idx:04d}"
        payment = random.choice(PAYMENT_METHODS)

        rows = []
        for it in basket:
            qty = QTY_BY_UNIT.get(it["unit"], lambda: 1)()
            # gentle inflation drift over the period + small day-to-day noise
            drift = 1 + 0.0008 * trip_idx
            noise = random.uniform(0.92, 1.08)
            unit_price = round(it["base_price"] * drift * noise, 2)

            if it["id"] == anomaly_id:
                unit_price = round(unit_price * random.choice([3.5, 4]), 2)

            total_price = round(unit_price * qty, 2)
            rows.append({
                "raw_name": it["item"] or it["id"],
                "matched_id": it["id"],
                "matched_category": it["category"],
                "matched_subcategory": it["subcategory"],
                "tags": it["tags"],
                "unit": it["unit"],
                "quantity": qty,
                "unit_price": unit_price,
                "total_price": total_price,
                "source": source,
                "order_id": order_id,
                "payment_method": payment,
                "datetime": d.isoformat(),
                "gpt_notes": "",
            })

        trips.append((d, source, rows))
        d += timedelta(days=random.choice([5, 6, 7, 7, 8, 9]))
        trip_idx += 1

    return trips


def write_review_csvs(trips, review_dir: Path):
    for d, source, rows in trips:
        fname = f"{d.strftime('%d_%b_%Y').lower()}_{source.lower().replace(' ', '_')}.csv"
        with open(review_dir / fname, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()

    items = load_items()
    trips = build_trips(items)

    with tempfile.TemporaryDirectory() as tmp:
        review_dir = Path(tmp)
        write_review_csvs(trips, review_dir)
        result = run_import(DB_PATH, review_dir)

    print(f"Built {DB_PATH.name}: {result['message']} across {result['files_processed']} trips.")
    print("Copy it to res_domus.db to try the demo:")
    print(f"  cp {DB_PATH.name} res_domus.db")


if __name__ == "__main__":
    main()

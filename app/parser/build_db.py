"""
Database builder — imports review CSVs into res_domus.db.
Export: run_import(db_path, review_dir)
"""

import csv
import re
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

DEDUP_COLS = ("matched_id", "datetime", "quantity", "total_price")

SCHEMA = """
CREATE TABLE IF NOT EXISTS purchases (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_name            TEXT,
    matched_id          TEXT,
    matched_category    TEXT,
    matched_subcategory TEXT,
    tags                TEXT,
    unit                TEXT,
    quantity            REAL,
    unit_price          REAL,
    total_price         REAL,
    source              TEXT,
    order_id            TEXT,
    payment_method      TEXT,
    datetime            DATE,
    gpt_notes           TEXT,
    import_ts           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_file         TEXT
);
CREATE INDEX IF NOT EXISTS idx_matched_id   ON purchases (matched_id);
CREATE INDEX IF NOT EXISTS idx_datetime     ON purchases (datetime);
CREATE INDEX IF NOT EXISTS idx_category     ON purchases (matched_category);
CREATE INDEX IF NOT EXISTS idx_subcategory  ON purchases (matched_subcategory);
CREATE INDEX IF NOT EXISTS idx_source       ON purchases (source);
CREATE INDEX IF NOT EXISTS idx_tags         ON purchases (tags);

CREATE TABLE IF NOT EXISTS budget (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    month           TEXT NOT NULL UNIQUE,
    manual_budget   REAL,
    notes           TEXT,
    updated_ts      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

VIEWS = """
DROP VIEW IF EXISTS v_item_stats;
CREATE VIEW v_item_stats AS
WITH base AS (
    SELECT matched_id, matched_category, matched_subcategory, tags, unit,
           unit_price, total_price, quantity, datetime, source,
           LAG(datetime) OVER (PARTITION BY matched_id ORDER BY datetime) AS prev_date
    FROM purchases
    WHERE matched_id IS NOT NULL AND raw_name != 'TOTAL'
),
intervals AS (
    SELECT matched_id,
           CAST(julianday(datetime) - julianday(prev_date) AS REAL) AS interval_days
    FROM base WHERE prev_date IS NOT NULL
),
stats AS (
    SELECT matched_id,
           COUNT(*)                                                           AS purchase_count,
           AVG(unit_price)                                                    AS avg_unit_price,
           MIN(unit_price)                                                    AS min_unit_price,
           MAX(unit_price)                                                    AS max_unit_price,
           SQRT(AVG(unit_price*unit_price) - AVG(unit_price)*AVG(unit_price)) AS std_unit_price,
           AVG(quantity)                                                      AS avg_quantity,
           SUM(total_price)                                                   AS total_spent,
           MAX(datetime)                                                      AS last_purchase_date,
           CAST(julianday('now') - julianday(MAX(datetime)) AS INTEGER)       AS days_since_last
    FROM base GROUP BY matched_id
),
ivl AS (
    SELECT matched_id, AVG(interval_days) AS avg_interval_days
    FROM intervals GROUP BY matched_id
),
last_qty AS (
    SELECT matched_id, SUM(quantity) AS last_quantity
    FROM base
    WHERE (matched_id, datetime) IN (SELECT matched_id, MAX(datetime) FROM base GROUP BY matched_id)
    GROUP BY matched_id
)
SELECT
    s.matched_id, b.matched_category, b.matched_subcategory, b.tags, b.unit,
    s.purchase_count,
    s.purchase_count >= 3                                                AS is_reliable,
    ROUND(s.avg_unit_price, 4)                                          AS avg_unit_price,
    ROUND(s.std_unit_price, 4)                                          AS std_unit_price,
    ROUND(s.min_unit_price, 4)                                          AS min_unit_price,
    ROUND(s.max_unit_price, 4)                                          AS max_unit_price,
    ROUND(s.avg_quantity, 4)                                            AS avg_quantity,
    ROUND(lq.last_quantity, 4)                                          AS last_quantity,
    ROUND(s.total_spent, 2)                                             AS total_spent,
    s.last_purchase_date, s.days_since_last,
    ROUND(ivl.avg_interval_days, 1)                                     AS avg_interval_days,
    CASE WHEN ivl.avg_interval_days > 0
         THEN ROUND(s.avg_quantity / ivl.avg_interval_days, 4) END      AS daily_consumption,
    CASE WHEN ivl.avg_interval_days > 0
         THEN ROUND(lq.last_quantity
              - (s.avg_quantity / ivl.avg_interval_days) * s.days_since_last, 4) END AS est_stock_remaining,
    CASE WHEN ivl.avg_interval_days > 0
         THEN ROUND(CAST(s.days_since_last AS REAL) / ivl.avg_interval_days, 3) END  AS reorder_urgency
FROM stats s
LEFT JOIN ivl      ON s.matched_id = ivl.matched_id
LEFT JOIN last_qty lq ON s.matched_id = lq.matched_id
LEFT JOIN (SELECT matched_id, matched_category, matched_subcategory, tags, unit FROM base GROUP BY matched_id) b
       ON s.matched_id = b.matched_id;

DROP VIEW IF EXISTS v_monthly_spend;
CREATE VIEW v_monthly_spend AS
SELECT strftime('%Y-%m', datetime) AS month, matched_category, matched_subcategory, matched_id,
       ROUND(SUM(total_price), 2) AS total_spent, COUNT(*) AS purchase_count
FROM purchases WHERE raw_name != 'TOTAL' AND datetime IS NOT NULL
GROUP BY month, matched_category, matched_subcategory, matched_id;

DROP VIEW IF EXISTS v_price_history;
CREATE VIEW v_price_history AS
SELECT matched_id, matched_category, matched_subcategory, unit, datetime, source,
       ROUND(unit_price, 4) AS unit_price, quantity, ROUND(total_price, 2) AS total_price, gpt_notes
FROM purchases WHERE raw_name != 'TOTAL' AND matched_id IS NOT NULL AND unit_price IS NOT NULL
ORDER BY matched_id, datetime;

DROP VIEW IF EXISTS v_anomalies;
CREATE VIEW v_anomalies AS
SELECT p.id, p.matched_id, p.matched_category, p.datetime, p.source,
       ROUND(p.unit_price, 4) AS unit_price,
       ROUND(s.avg_unit_price, 4) AS avg_unit_price,
       ROUND(s.std_unit_price, 4) AS std_unit_price,
       ROUND((p.unit_price - s.avg_unit_price) / NULLIF(s.std_unit_price, 0), 2) AS z_score,
       CASE WHEN p.unit_price > s.avg_unit_price THEN 'high' ELSE 'low' END AS direction,
       p.gpt_notes
FROM purchases p
JOIN v_item_stats s ON p.matched_id = s.matched_id
WHERE p.raw_name != 'TOTAL' AND p.unit_price IS NOT NULL AND s.std_unit_price > 0
  AND ABS((p.unit_price - s.avg_unit_price) / s.std_unit_price) > 3;

DROP VIEW IF EXISTS v_needed_soon;
CREATE VIEW v_needed_soon AS
SELECT matched_id, matched_category, matched_subcategory, tags, unit, last_purchase_date,
       days_since_last, ROUND(avg_interval_days, 1) AS avg_interval_days,
       ROUND(reorder_urgency, 3) AS reorder_urgency,
       ROUND(est_stock_remaining, 3) AS est_stock_remaining,
       ROUND(daily_consumption, 4) AS daily_consumption
FROM v_item_stats
WHERE is_reliable = 1 AND reorder_urgency IS NOT NULL AND reorder_urgency >= 0.8
ORDER BY reorder_urgency DESC;

DROP VIEW IF EXISTS v_stock_estimates;
CREATE VIEW v_stock_estimates AS
SELECT matched_id, matched_category, matched_subcategory, unit, last_purchase_date,
       days_since_last, ROUND(last_quantity, 4) AS last_quantity,
       ROUND(daily_consumption, 4) AS daily_consumption,
       ROUND(est_stock_remaining, 4) AS est_stock_remaining,
       ROUND(avg_interval_days, 1) AS avg_interval_days,
       ROUND(reorder_urgency, 3) AS reorder_urgency,
       CASE WHEN daily_consumption > 0
            THEN ROUND(est_stock_remaining / daily_consumption, 1) END AS days_of_stock_left
FROM v_item_stats
WHERE is_reliable = 1 AND daily_consumption IS NOT NULL AND daily_consumption > 0
ORDER BY reorder_urgency DESC;

DROP VIEW IF EXISTS v_budget;
CREATE VIEW v_budget AS
WITH monthly AS (
    SELECT strftime('%Y-%m', datetime) AS month, ROUND(SUM(total_price), 2) AS month_total
    FROM purchases WHERE raw_name != 'TOTAL' AND datetime >= date('now', '-18 months')
    GROUP BY month
),
baseline AS (SELECT ROUND(AVG(month_total), 2) AS avg_monthly_spend FROM monthly)
SELECT strftime('%Y-%m', 'now') AS current_month,
       bl.avg_monthly_spend AS avg_baseline,
       COALESCE(b.manual_budget, bl.avg_monthly_spend) AS effective_budget,
       b.manual_budget AS manual_override,
       ROUND(cur.month_total, 2) AS spent_this_month,
       ROUND(cur.month_total / NULLIF(COALESCE(b.manual_budget, bl.avg_monthly_spend), 0) * 100, 1) AS pct_of_budget
FROM baseline bl
LEFT JOIN monthly cur ON cur.month = strftime('%Y-%m', 'now')
LEFT JOIN budget b    ON b.month   = strftime('%Y-%m', 'now');

DROP VIEW IF EXISTS v_top_spenders;
CREATE VIEW v_top_spenders AS
SELECT matched_id, matched_category, matched_subcategory,
       ROUND(SUM(total_price), 2) AS total_spent_90d, COUNT(*) AS purchase_count
FROM purchases
WHERE raw_name != 'TOTAL' AND matched_id IS NOT NULL AND datetime >= date('now', '-90 days')
GROUP BY matched_id ORDER BY total_spent_90d DESC LIMIT 10;

DROP VIEW IF EXISTS v_price_by_source;
CREATE VIEW v_price_by_source AS
SELECT matched_id, unit, source, COUNT(*) AS purchase_count,
       ROUND(AVG(unit_price), 4) AS avg_unit_price,
       ROUND(MIN(unit_price), 4) AS min_unit_price,
       ROUND(MAX(unit_price), 4) AS max_unit_price
FROM purchases WHERE raw_name != 'TOTAL' AND matched_id IS NOT NULL AND unit_price IS NOT NULL
GROUP BY matched_id, source ORDER BY matched_id, avg_unit_price;
"""

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

DATE_PAT = re.compile(r"^(\d{2}_[a-z]{3}_\d{4})")


def _coerce_float(val) -> float | None:
    try:
        return float(val) if val not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


def _is_total_row(row: dict) -> bool:
    return (row.get("raw_name") or "").strip().upper() == "TOTAL"


NUMERIC_DEDUP_COLS = {"quantity", "total_price"}


def _normalize_dedup_value(col: str, value) -> str:
    if col in NUMERIC_DEDUP_COLS:
        try:
            return repr(float(value))
        except (TypeError, ValueError):
            pass
    return str(value or "").strip()


def _existing_dedup_keys(conn: sqlite3.Connection) -> set[tuple]:
    cur = conn.execute(f"SELECT {', '.join(DEDUP_COLS)} FROM purchases")
    return {
        tuple(_normalize_dedup_value(c, v) for c, v in zip(DEDUP_COLS, row))
        for row in cur.fetchall()
    }


def _build_dedup_key(row: dict) -> tuple:
    return tuple(_normalize_dedup_value(c, row.get(c)) for c in DEDUP_COLS)


def _import_csv(conn: sqlite3.Connection, path: Path, existing: set[tuple]) -> tuple[int, int, int]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    inserted = skipped_total = skipped_dup = 0
    for row in rows:
        if _is_total_row(row):
            skipped_total += 1
            continue
        key = _build_dedup_key(row)
        if key in existing:
            skipped_dup += 1
            continue
        conn.execute(INSERT_SQL, {
            "raw_name":          (row.get("raw_name") or "").strip() or None,
            "matched_id":        (row.get("matched_id") or "").strip() or None,
            "matched_category":  (row.get("matched_category") or "").strip() or None,
            "matched_subcategory": (row.get("matched_subcategory") or "").strip() or None,
            "tags":              (row.get("tags") or "").strip() or None,
            "unit":              (row.get("unit") or "").strip() or None,
            "quantity":          _coerce_float(row.get("quantity")),
            "unit_price":        _coerce_float(row.get("unit_price")),
            "total_price":       _coerce_float(row.get("total_price")),
            "source":            (row.get("source") or "").strip() or None,
            "order_id":          (row.get("order_id") or "").strip() or None,
            "payment_method":    (row.get("payment_method") or "").strip() or None,
            "datetime":          (row.get("datetime") or "").strip() or None,
            "gpt_notes":         (row.get("gpt_notes") or "").strip() or None,
            "source_file":       path.name,
        })
        existing.add(key)
        inserted += 1
    return inserted, skipped_total, skipped_dup


def _csv_item_count(path: Path) -> int:
    with open(path, newline="", encoding="utf-8") as f:
        return sum(1 for r in csv.DictReader(f) if (r.get("raw_name") or "").strip().upper() != "TOTAL")


def run_import(db_path: Path, review_dir: Path) -> dict:
    """Import all review CSVs into the DB. Returns a summary dict."""
    csv_files = sorted(review_dir.glob("*.csv"))
    if not csv_files:
        return {"inserted": 0, "skipped_dup": 0, "files_processed": 0, "message": "No CSV files found"}

    conn = sqlite3.connect(db_path)
    try:
        for col, typ in [("matched_subcategory", "TEXT"), ("tags", "TEXT")]:
            try:
                conn.execute(f"ALTER TABLE purchases ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        conn.executescript(SCHEMA)
        conn.executescript(VIEWS)

        existing = _existing_dedup_keys(conn)

        # File-level dedup: multiple CSVs for same date → keep largest non-TOTAL row count
        date_groups: dict[str, list[Path]] = defaultdict(list)
        for path in csv_files:
            m = DATE_PAT.match(path.name)
            key = m.group(1) if m else path.stem
            date_groups[key].append(path)

        selected: list[Path] = []
        for paths in sorted(date_groups.values(), key=lambda ps: ps[0].name):
            selected.append(max(paths, key=_csv_item_count) if len(paths) > 1 else paths[0])

        total_inserted = total_dup = 0
        for path in selected:
            inserted, _, skipped_dup = _import_csv(conn, path, existing)
            total_inserted += inserted
            total_dup += skipped_dup

        conn.commit()
    finally:
        conn.close()

    return {
        "inserted": total_inserted,
        "skipped_dup": total_dup,
        "files_processed": len(selected),
        "message": f"{total_inserted} rows inserted, {total_dup} duplicates skipped",
    }


def rebase_demo_dates(db_path) -> int:
    """If db_path is flagged as demo data (app_settings.demo_data == '1'), shift every
    purchases.datetime forward so the most recent purchase lands on today, preserving
    the spacing between trips. Without this, an unregenerated demo snapshot looks
    increasingly stale: Register's default 7-day filter reads as empty, and 'this
    month' KPIs/budget/stock-estimate views drift out of range. No-op for real
    (untagged) data and for demo data that's already current.

    Also re-keys the budget table's manual-override row (only
    scripts/generate_sample_db.py writes one) to the current 'YYYY-MM'.
    v_budget joins on an exact month-string match, and unlike a purchases
    row, a budget override has no shifting mechanism of its own. Left alone,
    it stops matching once the calendar rolls past its generation month, and
    the ring silently falls back to the raw baseline average.

    Returns the number of days shifted (0 if none)."""
    import db_settings

    db_path = str(db_path)
    if db_settings.get_setting(db_path, "demo_data") != "1":
        return 0

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT MAX(date(datetime)) FROM purchases").fetchone()
        if not row or not row[0]:
            return 0
        latest = date.fromisoformat(row[0])
        delta = (date.today() - latest).days
        if delta <= 0:
            return 0
        conn.execute("UPDATE purchases SET datetime = date(datetime, ?)", (f"+{delta} days",))
        conn.execute(
            "UPDATE budget SET month = strftime('%Y-%m', 'now') "
            "WHERE month != strftime('%Y-%m', 'now')"
        )
        conn.commit()
        return delta
    finally:
        conn.close()

"""
Dashboard endpoints: KPIs, spend trends, price history, anomalies, budget,
inventory estimates. Reads from the SQLite views built by parser/build_db.py.
"""

import calendar
import sqlite3
from datetime import date
from flask import Blueprint, jsonify, current_app

bp = Blueprint("dashboard", __name__, url_prefix="/api")

DELIVERY_CATEGORIES = {"Delivery", "Courier", "Servicio", "Service"}
_DELIVERY_SQL_LIST = "(" + ",".join(f"'{c}'" for c in sorted(DELIVERY_CATEGORIES)) + ")"


def _db():
    return sqlite3.connect(current_app.config["DB_PATH"])


def _rows_as_dicts(cur: sqlite3.Cursor) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _totals(conn: sqlite3.Connection, clause: str) -> tuple[float, int]:
    """(total_spent, order_count) for purchases matching an extra WHERE clause fragment."""
    row = conn.execute(f"""
        SELECT ROUND(SUM(total_price),2), COUNT(DISTINCT source || datetime)
        FROM purchases WHERE raw_name != 'TOTAL' AND total_price > 0 {clause}
    """).fetchone() or (0, 0)
    return row[0] or 0, row[1] or 0


@bp.get("/kpis")
def kpis():
    from flask import request as _req
    period = _req.args.get("period", "30d")
    conn = _db()
    today = date.today()
    cur_year  = today.strftime("%Y")
    prev_year = str(today.year - 1)

    if period == "all":
        # YTD vs same YTD last year
        ytd_clause  = f"AND strftime('%Y', datetime) = '{cur_year}'"
        pytd_clause = (
            f"AND strftime('%Y', datetime) = '{prev_year}' "
            f"AND strftime('%m-%d', datetime) <= '{today.strftime('%m-%d')}'"
        )
        cur_total,  cur_orders  = _totals(conn, ytd_clause)
        prev_total, prev_orders = _totals(conn, pytd_clause)
    else:
        _days = {"30d": 30, "90d": 90}.get(period, 30)
        date_clause = f"AND datetime >= date('now', '-{_days} days')"
        prev_clause = f"AND datetime >= date('now', '-{_days * 2} days') AND datetime < date('now', '-{_days} days')"
        cur_total,  cur_orders  = _totals(conn, date_clause)
        prev_total, prev_orders = _totals(conn, prev_clause)

    avg_order = round(cur_total / cur_orders, 2) if cur_orders else 0
    prev_avg  = round(prev_total / prev_orders, 2) if prev_orders else 0

    # Tracked items + category breakdown
    tracked = conn.execute(
        "SELECT COUNT(DISTINCT matched_id) FROM purchases WHERE matched_id IS NOT NULL"
    ).fetchone()[0]
    category_count = conn.execute(
        "SELECT COUNT(DISTINCT matched_category) FROM purchases "
        f"WHERE matched_id IS NOT NULL AND matched_category IS NOT NULL "
        f"AND matched_category NOT IN {_DELIVERY_SQL_LIST}"
    ).fetchone()[0]

    conn.close()

    def delta_pct(cur, prev):
        if not prev:
            return None
        return round((cur - prev) / prev * 100, 1)

    return jsonify({
        "month_total":    cur_total,
        "month_total_delta": delta_pct(cur_total, prev_total),
        "orders":         cur_orders,
        "orders_delta":   cur_orders - prev_orders,
        "avg_order":      avg_order,
        "avg_order_delta": delta_pct(avg_order, prev_avg),
        "tracked_items":  tracked,
        "category_count": category_count,
    })


@bp.get("/budget")
def budget():
    conn = _db()
    cur = conn.execute("SELECT * FROM v_budget")
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({})
    cols = [d[0] for d in cur.description]
    data = dict(zip(cols, row))

    today = date.today()
    data["days_remaining"] = calendar.monthrange(today.year, today.month)[1] - today.day

    # Rolling 30-day spend vs the same 18-month baseline average used for the
    # budget bar — doesn't reset at the calendar month boundary like
    # spent_this_month/effective_budget do, so it reads as a continuous trend.
    conn = _db()
    last_30d = conn.execute(
        "SELECT ROUND(SUM(total_price), 2) FROM purchases "
        "WHERE raw_name != 'TOTAL' AND datetime >= date('now', '-30 days')"
    ).fetchone()[0] or 0
    conn.close()
    baseline = data.get("avg_baseline") or 0
    data["last_30d_spend"] = last_30d
    data["deviation_pct"] = round((last_30d - baseline) / baseline * 100, 1) if baseline else None
    return jsonify(data)


@bp.get("/chart")
def chart():
    from flask import request
    period = request.args.get("period", "30d")
    conn = _db()

    MES = ['','ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic']

    def fmt_label_weekly(iso_date):
        try:
            from datetime import datetime as _dt
            d = _dt.fromisoformat(iso_date)
            return f"{d.day:02d} {MES[d.month]}"
        except Exception:
            return iso_date or ''

    def fmt_label_monthly(iso_month):
        try:
            parts = (iso_month or '').split('-')
            return MES[int(parts[1])] if len(parts) >= 2 else iso_month
        except Exception:
            return iso_month or ''

    def fmt_label_all(iso_month):
        try:
            parts = (iso_month or '').split('-')
            return f"{MES[int(parts[1])]} {parts[0]}" if len(parts) >= 2 else iso_month
        except Exception:
            return iso_month or ''

    DEL = _DELIVERY_SQL_LIST
    GROC = "('Abarrotes','Pantry')"
    MEAT = "('Carnes','Meat & Seafood')"

    SUMS = f"""
        ROUND(SUM(CASE WHEN matched_category NOT IN {DEL} THEN total_price ELSE 0 END), 2) AS total,
        ROUND(SUM(CASE WHEN matched_category IN {GROC} THEN total_price ELSE 0 END), 2) AS groceries,
        ROUND(SUM(CASE WHEN matched_category IN {MEAT} THEN total_price ELSE 0 END), 2) AS meat,
        ROUND(SUM(CASE WHEN matched_category IN {DEL} THEN total_price ELSE 0 END), 2) AS delivery
    """
    BASE = "FROM purchases WHERE raw_name != 'TOTAL' AND total_price > 0"

    # Each view targets ~15 points with bucket size proportional to the period,
    # so 30d→90d→all feel like coherent zoom-out levels of the same data.
    if period == "30d":
        # 2-day buckets → ~15 points
        rows = conn.execute(f"""
            SELECT CAST(julianday(datetime) / 2 AS INTEGER) AS bucket,
                   MIN(datetime) AS min_date, {SUMS}
            {BASE} AND datetime >= date('now', '-30 days')
            GROUP BY bucket ORDER BY bucket
        """).fetchall()
        rows = [(r[0], fmt_label_weekly(r[1]), r[2], r[3], r[4], r[5]) for r in rows]
    elif period == "90d":
        # 6-day buckets → ~15 points
        rows = conn.execute(f"""
            SELECT CAST(julianday(datetime) / 6 AS INTEGER) AS bucket,
                   MIN(datetime) AS min_date, {SUMS}
            {BASE} AND datetime >= date('now', '-90 days')
            GROUP BY bucket ORDER BY bucket
        """).fetchall()
        rows = [(r[0], fmt_label_weekly(r[1]), r[2], r[3], r[4], r[5]) for r in rows]
    else:  # all — monthly buckets → ~14 points
        rows = conn.execute(f"""
            SELECT strftime('%Y-%m', datetime) AS bucket,
                   MIN(datetime) AS min_date, {SUMS}
            {BASE}
            GROUP BY bucket ORDER BY bucket
        """).fetchall()
        rows = [(r[0], fmt_label_all(r[1]), r[2], r[3], r[4], r[5]) for r in rows]

    # Anomaly: most recent anomalous month — match against min_date of each bucket
    anomaly_row = conn.execute("""
        SELECT strftime('%Y-%m', datetime) AS bucket, COUNT(*) AS cnt
        FROM v_anomalies GROUP BY bucket ORDER BY bucket DESC LIMIT 1
    """).fetchone()

    conn.close()

    labels, totals, groceries, meat, delivery = [], [], [], [], []
    for r in rows:
        labels.append(r[1])
        totals.append(r[2] or 0)
        groceries.append(r[3] or 0)
        meat.append(r[4] or 0)
        delivery.append(r[5] or 0)

    delivery_above = [t + d for t, d in zip(totals, delivery)]

    anom_idx = None
    anom_label_es = anom_label_en = ""
    if anomaly_row:
        anom_month = anomaly_row[0][:7]  # YYYY-MM
        for i, r in enumerate(rows):
            min_date = str(r[1] or "")
            if min_date[:7] == anom_month:
                anom_idx = i
                anom_label_es = f"anomalia · {labels[i]}"
                anom_label_en = f"anomaly · {labels[i]}"
                break

    return jsonify({
        "labels":        labels,
        "total":         totals,
        "groceries":     groceries,
        "meat":          meat,
        "delivery":      delivery,
        "deliveryAbove": delivery_above,
        "anomalyIdx":    anom_idx,
        "anomalyLabel":  {"es": anom_label_es, "en": anom_label_en},
    })


@bp.get("/needed-soon")
def needed_soon():
    conn = _db()
    # 12 items in a deliberate display order: [red, red, yellow, yellow, yellow,
    # green, yellow, yellow, yellow, yellow, green, green].
    # Default-visible first 6 = 2 critical + 3 mid + 1 fine; see-more = next 6.
    cur = conn.execute(f"""
        WITH scored AS (
          SELECT matched_id, matched_category, last_purchase_date,
                 days_since_last, avg_interval_days, reorder_urgency,
                 est_stock_remaining, daily_consumption,
                 CASE
                   WHEN reorder_urgency >= 0.80 THEN 1
                   WHEN reorder_urgency >= 0.25 THEN 2
                   ELSE 3
                 END AS bucket
          FROM v_item_stats
          WHERE is_reliable = 1 AND reorder_urgency IS NOT NULL
            AND matched_category NOT IN {_DELIVERY_SQL_LIST}
        ),
        bucketed AS (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY reorder_urgency DESC) AS rn
          FROM scored
        )
        SELECT matched_id, matched_category, last_purchase_date,
               days_since_last, avg_interval_days, reorder_urgency,
               est_stock_remaining, daily_consumption
        FROM bucketed
        WHERE (bucket = 1 AND rn <= 2)
           OR (bucket = 2 AND rn <= 7)
           OR (bucket = 3 AND rn <= 3)
        ORDER BY
          CASE
            WHEN bucket = 1 THEN rn
            WHEN bucket = 2 AND rn <= 3 THEN rn + 2
            WHEN bucket = 3 AND rn = 1  THEN 6
            WHEN bucket = 2 AND rn > 3  THEN rn + 3
            ELSE rn + 9
          END
        LIMIT 12
    """)
    rows = _rows_as_dicts(cur)
    reliable_count = conn.execute(
        "SELECT COUNT(*) FROM v_item_stats WHERE is_reliable = 1"
    ).fetchone()[0]
    conn.close()

    for row in rows:
        urgency = row.get("reorder_urgency") or 0
        row["urgency_pct"] = min(100, round(urgency * 100))
        row["urgency_color"] = _urgency_color(row["urgency_pct"])
        # Days-left estimate: prefer stock-based if consumption is known,
        # fall back to cycle-based (avg_interval - days_since_last).
        est   = row.get("est_stock_remaining")
        daily = row.get("daily_consumption") or 0
        if daily > 0 and est is not None:
            row["days_left"] = round(est / daily)
        else:
            avg_int    = row.get("avg_interval_days") or 0
            days_since = row.get("days_since_last") or 0
            row["days_left"] = round(avg_int - days_since) if avg_int > 0 else None
    return jsonify({"items": rows, "reliable_count": reliable_count})


def _urgency_color(pct: int) -> str:
    if pct >= 80: return "#FF6F91"              # critical
    if pct >= 60: return "#FF9D6E"              # getting low
    if pct >= 40: return "#FFE0A3"              # watch it
    return "rgba(100,200,140,.8)"               # good stock — greenish


@bp.get("/top-items")
def top_items():
    from flask import request
    period = request.args.get("period", "30d")
    days = {"30d": 30, "90d": 90, "all": 36500}.get(period, 30)

    conn = _db()
    cur = conn.execute(f"""
        SELECT matched_id,
               ROUND(SUM(total_price), 2) AS total_spent,
               COUNT(*) AS purchase_count
        FROM purchases
        WHERE raw_name != 'TOTAL' AND matched_id IS NOT NULL
          AND matched_category NOT IN {_DELIVERY_SQL_LIST}
          AND datetime >= date('now', ? || ' days')
        GROUP BY matched_id
        ORDER BY total_spent DESC LIMIT 10
    """, (f"-{days}",))
    rows = _rows_as_dicts(cur)
    conn.close()

    if not rows:
        return jsonify([])

    grand_total = sum(r["total_spent"] for r in rows)
    top_total = rows[0]["total_spent"]
    colors = ["#F92672","#E6DB74","#66D9E8","#FD971F","#E6DB74","#A6E22E","#F92672","#66D9E8","#AE81FF","#FD971F"]
    for i, row in enumerate(rows):
        row["pct"] = round(row["total_spent"] / grand_total * 100) if grand_total else 0
        row["bar_width"] = round(row["total_spent"] / top_total * 100) if top_total else 0
        row["color"] = colors[i % len(colors)]

    return jsonify(rows)


@bp.get("/recent-orders")
def recent_orders():
    from flask import request as _req
    period = _req.args.get("period", "30d")
    _days = {"30d": 30, "90d": 90, "all": 36500}.get(period, 30)
    date_clause = f"AND datetime >= date('now', '-{_days} days')"

    conn = _db()
    cur = conn.execute(f"""
        SELECT datetime, source,
               ROUND(SUM(total_price), 2) AS order_total,
               COUNT(*) AS item_count,
               MIN(source_file) AS source_file
        FROM purchases
        WHERE raw_name != 'TOTAL' AND total_price > 0 {date_clause}
        GROUP BY datetime, source
        HAVING order_total > 0
        ORDER BY datetime DESC
        LIMIT 10
    """)
    rows = _rows_as_dicts(cur)
    conn.close()

    # Almost every row comes through the receipt-photo -> Claude vision -> CSV
    # pipeline (see parser/grocery_parser.py + parser/build_db.py) = "image".
    # Chat-logged purchases (see api/chat.py commit_purchase) are tagged with
    # source_file == "chat-entry" = "text". "import" (direct CSV, no photo)
    # has no data path yet.
    for row in rows:
        row["entry_type"] = "text" if row.get("source_file") == "chat-entry" else "image"

        # Format date as "DD mes"
        try:
            from datetime import datetime as dt
            d = dt.fromisoformat(row["datetime"])
            MES = ["","ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]
            row["date_label"] = f"{d.day:02d} {MES[d.month]}"
        except Exception:
            row["date_label"] = row["datetime"]

    return jsonify(rows)

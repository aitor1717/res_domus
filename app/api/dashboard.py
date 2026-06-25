"""
Dashboard endpoints: KPIs, spend trends, price history, anomalies, budget,
inventory estimates. Reads from the SQLite views built by parser/build_db.py.
"""

import sqlite3
from datetime import date, timedelta
from flask import Blueprint, jsonify, current_app

bp = Blueprint("dashboard", __name__, url_prefix="/api")

DELIVERY_CATEGORIES = {"Delivery", "Courier", "Servicio"}


def _db():
    return sqlite3.connect(current_app.config["DB_PATH"])


def _rows_as_dicts(cur: sqlite3.Cursor) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


@bp.get("/kpis")
def kpis():
    from flask import request as _req
    period = _req.args.get("period", "30d")
    _days = {"30d": 30, "90d": 90, "all": 36500}.get(period, 30)
    date_clause = f"AND datetime >= date('now', '-{_days} days')"
    prev_clause = f"AND datetime >= date('now', '-{_days * 2} days') AND datetime < date('now', '-{_days} days')"

    conn = _db()
    today = date.today()
    cur_month = today.strftime("%Y-%m")

    # Current period totals
    cur = conn.execute(f"""
        SELECT ROUND(SUM(total_price),2) AS total,
               COUNT(DISTINCT source || datetime) AS orders
        FROM purchases
        WHERE raw_name != 'TOTAL' {date_clause}
    """)
    cur_row = cur.fetchone() or (0, 0)
    cur_total, cur_orders = cur_row[0] or 0, cur_row[1] or 0

    # Previous same-length period totals
    prev = conn.execute(f"""
        SELECT ROUND(SUM(total_price),2) AS total,
               COUNT(DISTINCT source || datetime) AS orders
        FROM purchases
        WHERE raw_name != 'TOTAL' {prev_clause}
    """)
    prev_row = prev.fetchone() or (0, 0)
    prev_total, prev_orders = prev_row[0] or 0, prev_row[1] or 0

    avg_order = round(cur_total / cur_orders, 2) if cur_orders else 0
    prev_avg  = round(prev_total / prev_orders, 2) if prev_orders else 0

    # Tracked items
    tracked = conn.execute(
        "SELECT COUNT(DISTINCT matched_id) FROM purchases WHERE matched_id IS NOT NULL"
    ).fetchone()[0]
    new_this_month = conn.execute("""
        SELECT COUNT(DISTINCT matched_id) FROM (
            SELECT matched_id, MIN(strftime('%Y-%m', datetime)) AS first_month
            FROM purchases WHERE matched_id IS NOT NULL
            GROUP BY matched_id
        ) WHERE first_month = ?
    """, (cur_month,)).fetchone()[0]

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
        "new_this_month": new_this_month,
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
    days_in_month = (today.replace(month=today.month % 12 + 1, day=1) - timedelta(days=1)).day if today.month < 12 else 31
    data["days_remaining"] = days_in_month - today.day

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

    DELIVERY_CATS = tuple(DELIVERY_CATEGORIES)

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

    if period == "30d":
        rows = conn.execute("""
            SELECT strftime('%Y-%W', datetime) AS week,
                   MIN(datetime) AS min_date,
                   ROUND(SUM(CASE WHEN matched_category NOT IN ('Delivery','Courier','Servicio') THEN total_price ELSE 0 END), 2) AS total,
                   ROUND(SUM(CASE WHEN matched_category = 'Abarrotes' THEN total_price ELSE 0 END), 2) AS abarrotes,
                   ROUND(SUM(CASE WHEN matched_category = 'Carnes' THEN total_price ELSE 0 END), 2) AS carnes,
                   ROUND(SUM(CASE WHEN matched_category IN ('Delivery','Courier','Servicio') THEN total_price ELSE 0 END), 2) AS delivery
            FROM purchases
            WHERE raw_name != 'TOTAL' AND datetime >= date('now', '-30 days')
            GROUP BY week ORDER BY week
        """).fetchall()
        rows = [(r[0], fmt_label_weekly(r[1]), r[2], r[3], r[4], r[5]) for r in rows]
    elif period == "90d":
        rows = conn.execute("""
            SELECT strftime('%Y-%m', datetime) AS bucket,
                   strftime('%Y-%m', MIN(datetime)) AS min_month,
                   ROUND(SUM(CASE WHEN matched_category NOT IN ('Delivery','Courier','Servicio') THEN total_price ELSE 0 END), 2) AS total,
                   ROUND(SUM(CASE WHEN matched_category = 'Abarrotes' THEN total_price ELSE 0 END), 2) AS abarrotes,
                   ROUND(SUM(CASE WHEN matched_category = 'Carnes' THEN total_price ELSE 0 END), 2) AS carnes,
                   ROUND(SUM(CASE WHEN matched_category IN ('Delivery','Courier','Servicio') THEN total_price ELSE 0 END), 2) AS delivery
            FROM purchases
            WHERE raw_name != 'TOTAL' AND datetime >= date('now', '-90 days')
            GROUP BY bucket ORDER BY bucket
        """).fetchall()
        rows = [(r[0], fmt_label_monthly(r[1]), r[2], r[3], r[4], r[5]) for r in rows]
    else:  # all
        rows = conn.execute("""
            SELECT strftime('%Y-%m', datetime) AS bucket,
                   strftime('%Y-%m', MIN(datetime)) AS min_month,
                   ROUND(SUM(CASE WHEN matched_category NOT IN ('Delivery','Courier','Servicio') THEN total_price ELSE 0 END), 2) AS total,
                   ROUND(SUM(CASE WHEN matched_category = 'Abarrotes' THEN total_price ELSE 0 END), 2) AS abarrotes,
                   ROUND(SUM(CASE WHEN matched_category = 'Carnes' THEN total_price ELSE 0 END), 2) AS carnes,
                   ROUND(SUM(CASE WHEN matched_category IN ('Delivery','Courier','Servicio') THEN total_price ELSE 0 END), 2) AS delivery
            FROM purchases
            WHERE raw_name != 'TOTAL'
            GROUP BY bucket ORDER BY bucket
        """).fetchall()
        rows = [(r[0], fmt_label_all(r[1]), r[2], r[3], r[4], r[5]) for r in rows]

    # Anomaly: most recent anomalous week/month
    anomaly_row = conn.execute("""
        SELECT strftime('%Y-%m', datetime) AS bucket, COUNT(*) AS cnt
        FROM v_anomalies GROUP BY bucket ORDER BY bucket DESC LIMIT 1
    """).fetchone()

    conn.close()

    labels, totals, abarrotes, carnes, delivery = [], [], [], [], []
    for r in rows:
        labels.append(r[1])
        totals.append(r[2] or 0)
        abarrotes.append(r[3] or 0)
        carnes.append(r[4] or 0)
        delivery.append(r[5] or 0)

    delivery_above = [t + d for t, d in zip(totals, delivery)]

    # Find anomaly index in current period data
    anom_idx = None
    anom_label_es = anom_label_en = ""
    if anomaly_row:
        anom_bucket = anomaly_row[0]
        for i, r in enumerate(rows):
            if r[0].startswith(anom_bucket[:7]):
                anom_idx = i
                anom_label_es = f"anomalia · {labels[i]}"
                anom_label_en = f"anomaly · {labels[i]}"
                break

    return jsonify({
        "labels":       labels,
        "total":        totals,
        "abarrotes":    abarrotes,
        "carnes":       carnes,
        "delivery":     delivery,
        "deliveryAbove": delivery_above,
        "anomalyIdx":   anom_idx,
        "anomalyLabel": {"es": anom_label_es, "en": anom_label_en},
    })


@bp.get("/needed-soon")
def needed_soon():
    conn = _db()
    cur = conn.execute("""
        SELECT matched_id, matched_category, last_purchase_date,
               days_since_last, avg_interval_days, reorder_urgency
        FROM v_needed_soon LIMIT 15
    """)
    rows = _rows_as_dicts(cur)
    # Distinguish "all good, nothing urgent" from "not enough purchase
    # history yet to compute reliable insights" — both render an empty list
    # above but mean very different things to the user.
    reliable_count = conn.execute(
        "SELECT COUNT(*) FROM v_item_stats WHERE is_reliable = 1"
    ).fetchone()[0]
    conn.close()

    for row in rows:
        row["urgency_pct"] = min(100, round(row["reorder_urgency"] * 100))
        row["urgency_color"] = _urgency_color(row["urgency_pct"])
    return jsonify({"items": rows, "reliable_count": reliable_count})


def _urgency_color(pct: int) -> str:
    if pct >= 85: return "#FF6F91"
    if pct >= 70: return "#FF9D6E"
    if pct >= 55: return "#FFE0A3"
    if pct >= 45: return "rgba(255,157,110,.45)"
    if pct >= 30: return "rgba(255,157,110,.3)"
    return "rgba(255,157,110,.18)"


@bp.get("/top-items")
def top_items():
    from flask import request
    period = request.args.get("period", "30d")
    days = {"30d": 30, "90d": 90, "all": 36500}.get(period, 30)

    conn = _db()
    cur = conn.execute("""
        SELECT matched_id,
               ROUND(SUM(total_price), 2) AS total_spent,
               COUNT(*) AS purchase_count
        FROM purchases
        WHERE raw_name != 'TOTAL' AND matched_id IS NOT NULL
          AND matched_category NOT IN ('Delivery','Courier','Servicio')
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
        WHERE raw_name != 'TOTAL' {date_clause}
        GROUP BY datetime, source
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

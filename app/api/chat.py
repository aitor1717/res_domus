"""
Natural-language chat manager: question → Claude generates SQL → execute
against the DB → Claude formats a short answer. See parser/prompts.py for
the SQL-assistant system prompt and schema description.
"""

import calendar
import json
import re
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request

import anthropic
from flask import Blueprint, jsonify, request, current_app

from db_settings import get_anthropic_key
from parser.build_db import INSERT_SQL
from parser.grocery_parser import load_canonical_items
from parser.prompts import SQL_ASSISTANT_SYSTEM, build_chat_log_system, build_chat_log_user, build_sql_format_prompt

bp = Blueprint("chat", __name__, url_prefix="/api/chat")

CHAT_ENTRY_SOURCE_FILE = "chat-entry"
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

_SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SQL_STATEMENT_RE = re.compile(r"(SELECT\b.*)", re.DOTALL | re.IGNORECASE)
_RELIABILITY_GATED_RE = re.compile(r"v_needed_soon|v_stock_estimates|is_reliable", re.IGNORECASE)


def _extract_sql(text: str) -> str:
    """Claude sometimes wraps the SQL in a ```sql fence with prose around it,
    even though the system prompt asks for the bare query. Pull just the
    statement out so the SELECT-only check downstream isn't fooled."""
    text = text.strip()
    if text == "CANNOT_ANSWER":
        return text
    fence = _SQL_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    stmt = _SQL_STATEMENT_RE.search(text)
    if stmt:
        text = stmt.group(1).strip()
    if ";" in text:
        text = text.split(";")[0].strip()
    return text

NO_KEY_MSG = (
    "No Anthropic API key configured — add one in Settings to enable the chat manager. / "
    "No hay una clave API de Anthropic configurada — agrega una en Configuración para activar el chat."
)

CANNOT_ANSWER_MSG = {
    "en": "I can't answer that with the available data.",
    "es": "No puedo responder esa pregunta con los datos disponibles.",
}


def _db():
    return sqlite3.connect(current_app.config["DB_PATH"])


def _rows_as_dicts(cur: sqlite3.Cursor) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _coerce_float(val) -> float | None:
    try:
        return float(val) if val not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


def _extract_json_array(text: str) -> str:
    """Same defensive unwrap as _extract_sql — strip a ```json fence if Claude
    added one despite being asked not to."""
    text = text.strip()
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    return text


_PURCHASE_CUES_RE = re.compile(
    r"\b(bought|got|grabbed|picked\s+up|received|spent|spend|purchase[d]?|register|log|cost|paid|pay|"
    r"compr[éeo]|gast[éeo]|pagu[éeo]|registr[ao]|recib[io]|traje|llev[eéo]|cuesta|cuest[oó])\b"
    r"|\d",
    re.IGNORECASE,
)

_DATA_CUES_RE = re.compile(
    r"\b(spent|spend|pric|cost|total|budget|month|last|"
    r"how much|how many|categor|list|top|most|recent|order|stock|"
    r"running|low|when|what|which|where|averag|avg|histor|trend|"
    r"anomal|cheap|expens|buy|bought|inflat|sav|worth|store|market|"
    r"gast|preci|cuant|cuánt|barrat|caro|barato|presupuest)",
    re.IGNORECASE,
)

_SMALLTALK_REDIRECT = {
    "en": "Warehouse manager ready. Ask me about spending, prices, what's running low, or log a purchase.",
    "es": "Gestor listo. Pregunta sobre gastos, precios, artículos por agotar, o registra una compra.",
}


def _is_smalltalk(message: str) -> bool:
    """True when the message has no data-question cues and is short enough
    to be a greeting, ack, or meta-instruction rather than a real query."""
    words = message.split()
    if len(words) > 4:
        return False
    return not _DATA_CUES_RE.search(message) and not _PURCHASE_CUES_RE.search(message)


def _looks_like_purchase(message: str) -> bool:
    """Cheap local pre-filter before spending a model call on extraction —
    purchase-logging messages almost always mention an action word or a
    number (quantity/price); plain questions about the weather etc. won't."""
    return bool(_PURCHASE_CUES_RE.search(message))


def extract_purchase(message: str, db_path: str) -> dict:
    """Try to parse a free-text chat message ('I bought 200 eggs...') into
    draft purchase rows, reusing the same canonical-item matching as receipt
    parsing. Returns {"is_purchase": False} if the message isn't a logging
    statement, or {"is_purchase": True, "items": [...]} with draft rows that
    still need user confirmation before anything is written to the DB."""
    api_key = get_anthropic_key(db_path, current_app.config["ANTHROPIC_API_KEY"])
    if not api_key:
        return {"is_purchase": False}

    canonical_items = load_canonical_items(Path(current_app.config["AUX_CSV"]))
    system_prompt = build_chat_log_system(canonical_items)
    user_text = build_chat_log_user(message, date.today().isoformat())

    client = anthropic.Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            # Text-only field extraction, not vision — same complexity tier as
            # the NL->SQL chat, so this runs on the cheap model, not the
            # receipt-vision one.
            model=current_app.config["MODEL_CHAT"],
            max_tokens=1024,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_text}],
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"Claude API error: {e}") from e

    text = _extract_json_array(resp.content[0].text)
    if text.strip().upper().startswith("NOT_A_PURCHASE"):
        return {"is_purchase": False}

    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        return {"is_purchase": False}
    if not isinstance(items, list) or not items:
        return {"is_purchase": False}
    return {"is_purchase": True, "items": items}


def commit_purchase(items: list[dict], db_path: str) -> int:
    """Insert confirmed draft rows. unit_price is always recomputed from
    total_price/quantity here, matching the schema invariant — never trusts
    whatever value the LLM or the editable preview happened to carry."""
    conn = sqlite3.connect(db_path)
    inserted = 0
    for item in items:
        raw_name = (item.get("raw_name") or "").strip()
        entry_date = (item.get("datetime") or "").strip()
        quantity = _coerce_float(item.get("quantity"))
        total_price = _coerce_float(item.get("total_price"))
        if not raw_name or not entry_date or quantity is None or total_price is None:
            continue
        unit_price = round(total_price / quantity, 4) if quantity else None
        conn.execute(INSERT_SQL, {
            "raw_name": raw_name,
            "matched_id": (item.get("matched_id") or "").strip() or None,
            "matched_category": (item.get("matched_category") or "").strip() or None,
            "matched_subcategory": (item.get("matched_subcategory") or "").strip() or None,
            "tags": (item.get("tags") or "").strip() or None,
            "unit": (item.get("unit") or "").strip() or None,
            "quantity": quantity,
            "unit_price": unit_price,
            "total_price": total_price,
            "source": (item.get("source") or "").strip() or None,
            "order_id": None,
            "payment_method": None,
            "datetime": entry_date,
            "gpt_notes": (item.get("gpt_notes") or "").strip() or None,
            "source_file": CHAT_ENTRY_SOURCE_FILE,
        })
        inserted += 1
    conn.commit()
    conn.close()
    return inserted


def _ntfy_push(topic: str, message: str) -> None:
    try:
        req = Request(
            f"https://ntfy.sh/{topic}",
            data=message.encode(),
            headers={"Title": "res_domus note"},
        )
        urlopen(req, timeout=5)
    except Exception:
        pass


def _log_chat_entry(question: str) -> None:
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "question": question}
    with open(current_app.config["CHAT_LOG_PATH"], "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    topic = current_app.config.get("NTFY_TOPIC")
    if topic:
        t = threading.Thread(target=_ntfy_push, args=(topic, question), daemon=True)
        t.start()


def _get_context_prefix(db_path: str) -> str:
    """Build a short date+budget snippet injected as the first turn of every chat session."""
    today = date.today()
    days_left = calendar.monthrange(today.year, today.month)[1] - today.day
    ctx = (f"[CONTEXT] Today: {today.isoformat()}. "
           f"Current month: {today.strftime('%Y-%m')}. "
           f"{days_left} days remaining this month.")
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT spent_this_month, effective_budget, pct_of_budget FROM v_budget"
        ).fetchone()
        conn.close()
        if row and row[1]:
            spent, budget, pct = row
            ctx += (f" Budget: S/.{budget:.2f}. "
                    f"Spent so far: S/.{spent or 0:.2f} ({pct or 0}%).")
    except Exception:
        pass
    return ctx


def answer_question(question: str, db_path: str, lang: str = "en", history: list | None = None) -> dict:
    """Run the full NL→SQL→answer pipeline. Returns {answer, sql, rows}."""
    api_key = get_anthropic_key(db_path, current_app.config["ANTHROPIC_API_KEY"])
    if not api_key:
        return {"answer": NO_KEY_MSG, "sql": None, "rows": [], "no_api_key": True}

    if _is_smalltalk(question):
        return {"answer": _SMALLTALK_REDIRECT.get(lang, _SMALLTALK_REDIRECT["en"]), "sql": None, "rows": []}

    client = anthropic.Anthropic(api_key=api_key)
    model = current_app.config["MODEL_CHAT"]
    system_block = [{"type": "text", "text": SQL_ASSISTANT_SYSTEM, "cache_control": {"type": "ephemeral"}}]

    # Build messages: context prefix + prior history (last 6 turns) + current question
    context_prefix = _get_context_prefix(db_path)
    messages: list[dict] = [
        {"role": "user",      "content": context_prefix},
        {"role": "assistant", "content": "Ready."},
    ]
    for turn in (history or [])[-6:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})

    try:
        sql_resp = client.messages.create(
            model=model, max_tokens=512,
            system=system_block, messages=messages,
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"Claude API error: {e}") from e
    sql = _extract_sql(sql_resp.content[0].text)

    sql_failed = sql == "CANNOT_ANSWER" or not sql.upper().startswith("SELECT")
    rows = []
    if not sql_failed:
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute(sql)
            rows = _rows_as_dicts(cur)
        except sqlite3.Error as db_err:
            # Retry once with the error message so Claude can correct the query
            retry_msgs = messages + [
                {"role": "assistant", "content": sql},
                {"role": "user",      "content": f"That query failed: {db_err}. Generate a corrected SELECT query."},
            ]
            try:
                retry_resp = client.messages.create(
                    model=model, max_tokens=512,
                    system=system_block, messages=retry_msgs,
                )
                sql2 = _extract_sql(retry_resp.content[0].text)
                if sql2 != "CANNOT_ANSWER" and sql2.upper().startswith("SELECT"):
                    cur2 = conn.execute(sql2)
                    rows = _rows_as_dicts(cur2)
                    sql = sql2
                else:
                    sql_failed = True
            except Exception:
                sql_failed = True
        finally:
            conn.close()

    if sql_failed:
        # Before giving up, check whether this was actually a purchase-logging
        # statement ("I bought eggs...") rather than a question — covers both
        # an explicit CANNOT_ANSWER and a SQL attempt that just didn't run
        # (the model sometimes tries to query a logging statement instead of
        # declining it). Cheap local gate first — only pay for the extraction
        # call when the message actually looks purchase-related, so unrelated
        # cannot-answer questions ("what's the weather") don't burn tokens.
        if _looks_like_purchase(question):
            draft = extract_purchase(question, db_path)
            if draft.get("is_purchase"):
                return {"answer": None, "sql": None, "rows": [], "draft_purchase": draft["items"]}
        msg = CANNOT_ANSWER_MSG.get(lang, CANNOT_ANSWER_MSG["en"])
        return {"answer": msg, "sql": None, "rows": []}

    low_data_hint = not rows and bool(_RELIABILITY_GATED_RE.search(sql))
    format_prompt = build_sql_format_prompt(question, sql, rows, low_data_hint, lang)
    try:
        fmt_resp = client.messages.create(
            model=model,
            max_tokens=180,
            messages=[{"role": "user", "content": format_prompt}],
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"Claude API error: {e}") from e
    return {"answer": fmt_resp.content[0].text.strip(), "sql": sql, "rows": rows}


def _get_notice(db_path: str, lang: str = "en") -> str | None:
    """Return one terse session-opener notice if something is noteworthy, else None."""
    try:
        conn = sqlite3.connect(db_path)
        today = date.today()
        days_left = calendar.monthrange(today.year, today.month)[1] - today.day

        # Priority 1: budget >= 80%
        row = conn.execute(
            "SELECT pct_of_budget, effective_budget FROM v_budget"
        ).fetchone()
        if row and row[0] and row[0] >= 80:
            pct = int(row[0])
            if lang == "es":
                return f"Presupuesto al {pct}% — quedan {days_left} días."
            return f"Budget at {pct}% — {days_left} days remaining."

        # Priority 2: price anomaly in last 7 days
        row = conn.execute(
            "SELECT matched_id, direction, datetime FROM v_anomalies "
            "WHERE datetime >= date('now','-7 days') "
            "ORDER BY datetime DESC LIMIT 1"
        ).fetchone()
        if row:
            item, direction, dt = row
            if lang == "es":
                adj = "inusualmente alto" if direction == "high" else "inusualmente bajo"
                return f"{item} tuvo un precio {adj} el {dt}."
            adj = "unusually high" if direction == "high" else "unusually low"
            return f"{item} had a {adj} price on {dt}."

        # Priority 3: item 90+ days overdue
        row = conn.execute(
            "SELECT matched_id, ROUND(days_since_last - avg_interval_days, 0) AS overdue "
            "FROM v_item_stats "
            "WHERE is_reliable = 1 AND days_since_last - avg_interval_days > 90 "
            "  AND LOWER(COALESCE(matched_category,'')) NOT IN ('delivery','courier','servicio') "
            "ORDER BY overdue DESC LIMIT 1"
        ).fetchone()
        if row:
            item, days = row[0], int(row[1])
            if lang == "es":
                return f"{item} lleva {days} días de retraso."
            return f"{item} is {days} days overdue."

        conn.close()
    except Exception:
        pass
    return None


@bp.get("/notice")
def chat_notice():
    lang = request.args.get("lang", "en")
    notice = _get_notice(current_app.config["DB_PATH"], lang)
    return jsonify({"notice": notice})


@bp.post("/query")
def query():
    data = request.get_json(force=True)
    question = (data.get("question") or "").strip()
    lang = data.get("lang") or "en"
    history = data.get("history") or []
    if not question:
        return jsonify({"error": "question required"}), 400

    _log_chat_entry(question)

    try:
        result = answer_question(question, current_app.config["DB_PATH"], lang, history)
    except RuntimeError as e:
        current_app.logger.exception("chat query failed")
        return jsonify({"error": str(e)}), 500

    return jsonify(result)


@bp.post("/commit-purchase")
def commit_purchase_route():
    data = request.get_json(force=True)
    items = data.get("items") or []
    if not items:
        return jsonify({"error": "items required"}), 400
    inserted = commit_purchase(items, current_app.config["DB_PATH"])
    return jsonify({"inserted": inserted})


@bp.get("/log")
def chat_log():
    log_path = current_app.config["CHAT_LOG_PATH"]
    entries = []
    try:
        with open(log_path) as f:
            for line in f:
                e = json.loads(line)
                entries.append(e)
    except FileNotFoundError:
        pass
    lines = "\n".join(
        f"[{e['timestamp'][:10]}] {e['question']}" for e in reversed(entries)
    )
    return lines or "no entries yet", 200, {"Content-Type": "text/plain; charset=utf-8"}

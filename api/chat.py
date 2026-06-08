import sqlite3
import anthropic
from flask import Blueprint, jsonify, request, current_app

from parser.prompts import SQL_ASSISTANT_SYSTEM, build_sql_format_prompt

bp = Blueprint("chat", __name__, url_prefix="/api/chat")


def _db():
    return sqlite3.connect(current_app.config["DB_PATH"])


def _rows_as_dicts(cur: sqlite3.Cursor) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def answer_question(question: str, db_path: str) -> dict:
    """Run the full NL→SQL→answer pipeline. Returns {answer, sql, rows}."""
    client = anthropic.Anthropic(api_key=current_app.config["ANTHROPIC_API_KEY"])

    sql_resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=SQL_ASSISTANT_SYSTEM,
        messages=[{"role": "user", "content": question}],
    )
    sql = sql_resp.content[0].text.strip()

    if sql == "CANNOT_ANSWER":
        return {"answer": "No puedo responder esa pregunta con los datos disponibles.", "sql": None, "rows": []}

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(sql)
        rows = _rows_as_dicts(cur)
    except sqlite3.Error as e:
        conn.close()
        raise RuntimeError(f"SQL error: {e}") from e
    conn.close()

    format_prompt = build_sql_format_prompt(question, sql, rows)
    fmt_resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        messages=[{"role": "user", "content": format_prompt}],
    )
    return {"answer": fmt_resp.content[0].text.strip(), "sql": sql, "rows": rows}


@bp.post("/query")
def query():
    data = request.get_json(force=True)
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400

    try:
        result = answer_question(question, current_app.config["DB_PATH"])
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    return jsonify(result)

"""
Upload flow with Server-Sent Events.

POST /api/upload/files        → saves images, spawns thread, returns {session_id}
GET  /api/upload/parse-status/<sid> → SSE stream
POST /api/upload/confirm-date → unblocks parse thread with confirmed date
POST /api/upload/confirm-parse → runs build_db, commits to DB
POST /api/upload/retry-parse  → re-runs parse with override note
"""

import json
import queue
import threading
import time
import uuid
from pathlib import Path

import anthropic
from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from db_settings import get_anthropic_key
from parser.grocery_parser import (
    archive_images,
    clean_items,
    flag_outliers,
    infer_date_from_group,
    load_canonical_items,
    load_price_stats,
    parse_group,
    save_review,
)
from parser.build_db import run_import
from parser.prompts import build_parser_system, build_parser_user

bp = Blueprint("upload", __name__, url_prefix="/api/upload")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

NO_KEY_MSG = (
    "No Anthropic API key configured — add one in Settings to enable receipt parsing. / "
    "No hay una clave API de Anthropic configurada — agrega una en Configuración para habilitar el análisis de recibos."
)

# Per-session state: sid → {sse_queue, date_queue, items, images, group_name, order_date}
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()


def _session(sid: str) -> dict | None:
    with _sessions_lock:
        return _sessions.get(sid)


def _emit(q: queue.Queue, event: str, data: dict) -> None:
    q.put(f"event: {event}\ndata: {json.dumps(data)}\n\n")


def _parse_thread(sid: str, app) -> None:
    with app.app_context():
        sess = _session(sid)
        if not sess:
            return
        sq = sess["sse_queue"]
        dq = sess["date_queue"]

        try:
            # Infer date
            inferred = infer_date_from_group(sess["group_name"], sess["images"])
            date_str = inferred.isoformat() if inferred else None
            _emit(sq, "date", {"value": date_str, "inferred": date_str is not None})

            # Block until date confirmed (5-minute timeout)
            try:
                confirmed_date_str = dq.get(timeout=300)
            except queue.Empty:
                _emit(sq, "error", {"message": "Timeout waiting for date confirmation"})
                return

            sess["order_date"] = confirmed_date_str

            # Load canonical items and price stats
            aux_csv = Path(current_app.config["AUX_CSV"])
            db_path = Path(current_app.config["DB_PATH"])
            canonical_items = load_canonical_items(aux_csv)
            price_stats = load_price_stats(db_path)

            # Build prompt and parse
            system_prompt = build_parser_system(canonical_items)
            user_text = build_parser_user(confirmed_date_str)
            _emit(sq, "progress", {"message": "Parsing receipt…"})

            api_key = get_anthropic_key(current_app.config["DB_PATH"], current_app.config["ANTHROPIC_API_KEY"])
            client = anthropic.Anthropic(api_key=api_key)
            items = parse_group(client, sess["images"], system_prompt, user_text, model=current_app.config["MODEL_PARSER"])
            items = clean_items(items, confirmed_date_str)
            flag_outliers(items, price_stats)

            sess["items"] = items
            _emit(sq, "done", {"items": items, "count": len(items)})

        except Exception as e:
            current_app.logger.exception("parse thread failed")
            _emit(sq, "error", {"message": str(e)})
        finally:
            sq.put(None)  # sentinel — close stream


@bp.post("/files")
def upload_files():
    if not get_anthropic_key(current_app.config["DB_PATH"], current_app.config["ANTHROPIC_API_KEY"]):
        return jsonify({"error": NO_KEY_MSG}), 400

    files = request.files.getlist("images")
    if not files:
        return jsonify({"error": "no images uploaded"}), 400

    upload_dir = Path(current_app.config["UPLOAD_DIR"])
    upload_dir.mkdir(parents=True, exist_ok=True)

    sid = uuid.uuid4().hex
    group_name = f"upload_{sid[:8]}"
    group_dir = upload_dir / group_name
    group_dir.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in IMAGE_EXTS:
            continue
        dest = group_dir / f"{len(saved):02d}{ext}"
        f.save(dest)
        saved.append(dest)

    if not saved:
        return jsonify({"error": "no valid image files"}), 400

    with _sessions_lock:
        _sessions[sid] = {
            "sse_queue":  queue.Queue(),
            "date_queue": queue.Queue(maxsize=1),
            "images":     saved,
            "group_name": group_name,
            "items":      None,
            "order_date": None,
        }

    app = current_app._get_current_object()
    t = threading.Thread(target=_parse_thread, args=(sid, app), daemon=True)
    t.start()

    return jsonify({"session_id": sid})


@bp.get("/parse-status/<sid>")
def parse_status(sid: str):
    sess = _session(sid)
    if not sess:
        return jsonify({"error": "session not found"}), 404

    @stream_with_context
    def generate():
        sq = sess["sse_queue"]
        while True:
            try:
                msg = sq.get(timeout=25)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if msg is None:
                break
            yield msg

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bp.post("/confirm-date")
def confirm_date():
    data = request.get_json(force=True)
    sid = data.get("session_id")
    date_str = data.get("date")
    sess = _session(sid)
    if not sess:
        return jsonify({"error": "session not found"}), 404
    try:
        sess["date_queue"].put_nowait(date_str)
    except queue.Full:
        return jsonify({"error": "date already confirmed"}), 409
    return jsonify({"ok": True})


@bp.post("/confirm-parse")
def confirm_parse():
    data = request.get_json(force=True)
    sid = data.get("session_id")
    items = data.get("items")  # edited items from review table
    sess = _session(sid)
    if not sess:
        return jsonify({"error": "session not found"}), 404

    final_items = items if items is not None else sess.get("items") or []
    order_date = sess.get("order_date")
    if not order_date:
        return jsonify({"error": "no confirmed date in session"}), 400

    review_dir = Path(current_app.config["REVIEW_DIR"])
    db_path = Path(current_app.config["DB_PATH"])

    from datetime import date as _date
    d = _date.fromisoformat(order_date)
    csv_path = save_review(sess["group_name"], d, final_items, [], review_dir)

    result = run_import(db_path, review_dir)

    # Archive images
    if sess.get("images"):
        archive_images(
            sess["images"],
            sess["group_name"],
            Path(current_app.config["ARCHIVE_DIR"]),
            Path(current_app.config["UPLOAD_DIR"]),
        )

    with _sessions_lock:
        _sessions.pop(sid, None)

    return jsonify({**result, "csv": csv_path.name})


@bp.post("/retry-parse")
def retry_parse():
    data = request.get_json(force=True)
    sid = data.get("session_id")
    note = data.get("note", "")
    sess = _session(sid)
    if not sess:
        return jsonify({"error": "session not found"}), 404

    order_date = sess.get("order_date")
    if not order_date:
        return jsonify({"error": "no confirmed date"}), 400

    aux_csv = Path(current_app.config["AUX_CSV"])
    db_path = Path(current_app.config["DB_PATH"])
    canonical_items = load_canonical_items(aux_csv)
    price_stats = load_price_stats(db_path)

    system_prompt = build_parser_system(canonical_items)
    user_text = build_parser_user(order_date, note)

    api_key = get_anthropic_key(current_app.config["DB_PATH"], current_app.config["ANTHROPIC_API_KEY"])
    if not api_key:
        return jsonify({"error": NO_KEY_MSG}), 400

    client = anthropic.Anthropic(api_key=api_key)
    try:
        items = parse_group(client, sess["images"], system_prompt, user_text, model=current_app.config["MODEL_PARSER"])
        items = clean_items(items, order_date)
        flag_outliers(items, price_stats)
        sess["items"] = items
        return jsonify({"items": items, "count": len(items)})
    except Exception as e:
        current_app.logger.exception("retry-parse failed")
        return jsonify({"error": str(e)}), 500

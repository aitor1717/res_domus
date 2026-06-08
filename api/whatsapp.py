"""
WhatsApp Cloud API webhook for Res Domus.

Supported flows:
  - Image message  → receipt parse → confirmation reply
  - Text message   → intent detection → entry parse OR NL query reply
  - GET /webhook   → webhook verification (Meta setup)
"""

import os
import re
import json
import logging
import requests
from pathlib import Path
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app

bp = Blueprint("whatsapp", __name__, url_prefix="/webhook")
log = logging.getLogger(__name__)

GRAPH_API = "https://graph.facebook.com/v19.0"

# Simple heuristic: has a number + a word that looks like currency/quantity → purchase entry
_ENTRY_RE = re.compile(r"\d", re.IGNORECASE)
_QUERY_RE = re.compile(
    r"\b(cuánto|cuanto|cuántos|cuantos|cuál|cual|qué|que|how much|how many|what|show|list|dame|muéstrame|gastar|gasté|gaste|total|historial|history)\b",
    re.IGNORECASE,
)


# ─── Verification ────────────────────────────────────────────────────────────

@bp.get("/whatsapp")
def verify():
    verify_token = current_app.config.get("WHATSAPP_VERIFY_TOKEN", "")
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == verify_token:
        return challenge, 200
    return "Forbidden", 403


# ─── Incoming messages ───────────────────────────────────────────────────────

@bp.post("/whatsapp")
def receive():
    data = request.get_json(silent=True) or {}
    try:
        entry  = data["entry"][0]
        change = entry["changes"][0]["value"]
        msg    = change["messages"][0]
    except (KeyError, IndexError):
        return jsonify({"status": "ok"}), 200

    from_number = msg.get("from", "")
    msg_type    = msg.get("type", "")

    if msg_type == "text":
        text = msg.get("text", {}).get("body", "").strip()
        _handle_text(from_number, text)

    elif msg_type == "image":
        image_id = msg.get("image", {}).get("id", "")
        caption  = msg.get("image", {}).get("caption", "").strip()
        _handle_image(from_number, image_id, caption)

    return jsonify({"status": "ok"}), 200


# ─── Handlers ────────────────────────────────────────────────────────────────

def _handle_text(from_number: str, text: str):
    if not text:
        return
    # Route: query intent takes priority if keywords found
    if _QUERY_RE.search(text) and not _looks_like_entry(text):
        _handle_query(from_number, text)
    else:
        _handle_entry(from_number, text)


def _looks_like_entry(text: str) -> bool:
    """Heuristic: has digits and at least one non-question word."""
    return bool(_ENTRY_RE.search(text)) and not text.strip().endswith("?")


def _handle_entry(from_number: str, text: str):
    """Parse a text purchase entry and reply with a confirmation."""
    try:
        from parser.grocery_parser import parse_text_entry
        items = parse_text_entry(
            text,
            db_path=current_app.config["DB_PATH"],
            aux_csv=current_app.config["AUX_CSV"],
        )
    except Exception as exc:
        log.error("Text entry parse failed: %s", exc)
        _send_text(from_number, "❌ No pude entender la entrada. Intenta: *2 leche 3.50 wong ayer*")
        return

    if not items:
        _send_text(from_number, "❓ No encontré ítems. Formato: *[cant] [ítem] [precio] [tienda?] [fecha?]*")
        return

    lines = []
    for it in items:
        qty   = it.get("quantity", 1)
        name  = it.get("matched_id") or it.get("raw_name", "—")
        price = it.get("total_price", 0)
        lines.append(f"  • {qty}× {name} — S/. {price:.2f}")

    preview = "\n".join(lines)
    _send_text(from_number,
        f"🧾 *Entrada detectada:*\n{preview}\n\n¿Confirmar? Responde *sí* para importar o *no* para cancelar."
    )
    # TODO: store pending entry keyed by from_number for a follow-up confirm


def _handle_image(from_number: str, image_id: str, caption: str):
    """Download image, run receipt parse, reply with item list."""
    access_token = current_app.config.get("WHATSAPP_ACCESS_TOKEN", "")
    if not access_token:
        _send_text(from_number, "⚠️ No configurado: WHATSAPP_ACCESS_TOKEN faltante.")
        return

    # Download image bytes from Graph API
    try:
        meta_url = f"{GRAPH_API}/{image_id}"
        meta = requests.get(meta_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
        meta.raise_for_status()
        download_url = meta.json()["url"]

        img_resp = requests.get(download_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
        img_resp.raise_for_status()
    except Exception as exc:
        log.error("Image download failed: %s", exc)
        _send_text(from_number, "❌ Error descargando la imagen. Intenta de nuevo.")
        return

    # Save to upload dir
    upload_dir = Path(current_app.config["UPLOAD_DIR"])
    ts = datetime.now().strftime("%d_%b_%Y").lower()
    img_path = upload_dir / f"{ts}_wa_{image_id[:8]}.jpg"
    img_path.write_bytes(img_resp.content)

    _send_text(from_number, "📷 Imagen recibida. Procesando recibo…")

    try:
        from parser.grocery_parser import (
            load_canonical_items, parse_group, clean_items, flag_outliers, load_price_stats,
        )
        canonical = load_canonical_items(current_app.config["AUX_CSV"])
        date_hint = caption or ts
        raw_items = parse_group(
            [str(img_path)],
            date_hint,
            canonical,
            api_key=current_app.config["ANTHROPIC_API_KEY"],
        )
        price_stats = load_price_stats(current_app.config["DB_PATH"])
        items = clean_items(raw_items, canonical)
        items = flag_outliers(items, price_stats)
    except Exception as exc:
        log.error("Receipt parse failed: %s", exc)
        _send_text(from_number, "❌ No pude analizar el recibo. Intenta con una imagen más clara.")
        return

    if not items:
        _send_text(from_number, "🤷 No encontré ítems en el recibo.")
        return

    lines = []
    for it in items:
        qty   = it.get("quantity", 1)
        name  = it.get("matched_id") or it.get("raw_name", "—")
        price = it.get("total_price", 0)
        flag  = " ⚠️" if it.get("flag") else ""
        lines.append(f"  • {qty}× {name} — S/. {price:.2f}{flag}")

    preview = "\n".join(lines[:15])
    if len(items) > 15:
        preview += f"\n  … y {len(items) - 15} más"

    _send_text(from_number,
        f"🧾 *{len(items)} ítems detectados:*\n{preview}\n\n¿Importar? Responde *sí* o *no*."
    )


def _handle_query(from_number: str, question: str):
    """Route to SQL chat pipeline and reply with the answer."""
    try:
        from api.chat import answer_question
        result = answer_question(question, current_app.config["DB_PATH"])
        reply  = result.get("answer", "Sin respuesta.")
    except Exception as exc:
        log.error("Query failed: %s", exc)
        reply = "❌ Error procesando tu consulta."

    _send_text(from_number, reply)


# ─── Send helpers ─────────────────────────────────────────────────────────────

def _send_text(to: str, body: str):
    phone_id     = current_app.config.get("WHATSAPP_PHONE_ID", "")
    access_token = current_app.config.get("WHATSAPP_ACCESS_TOKEN", "")
    if not phone_id or not access_token:
        log.warning("WhatsApp not configured — message not sent: %s", body[:80])
        return

    url = f"{GRAPH_API}/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    try:
        r = requests.post(url, json=payload, headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }, timeout=10)
        r.raise_for_status()
    except Exception as exc:
        log.error("WhatsApp send failed: %s", exc)

import os
from pathlib import Path

# Copy this file to config.py and fill in your values.
# config.py is gitignored — never commit it.

BASE_DIR = Path(__file__).resolve().parent

# API key — reads from environment variable first, falls back to the value below.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "sk-ant-...")

# Paths
DB_PATH     = BASE_DIR / "res_domus.db"
AUX_CSV     = BASE_DIR / "aux_items.csv"
UPLOAD_DIR  = BASE_DIR / "input"
REVIEW_DIR  = BASE_DIR / "review"
ARCHIVE_DIR = BASE_DIR / "archive"
OUTPUT_DIR  = BASE_DIR / "output"

# Flask
SECRET_KEY  = os.environ.get("SECRET_KEY", "change-me-in-production")
DEBUG       = os.environ.get("FLASK_DEBUG", "0") == "1"

# Turso (optional — leave blank to use local SQLite)
# Get from: turso db show <db-name> --url  and  turso db tokens create <db-name>
TURSO_URL   = os.environ.get("TURSO_URL", "")
TURSO_TOKEN = os.environ.get("TURSO_TOKEN", "")

# WhatsApp Cloud API (optional — leave blank to disable webhook)
# Get these from Meta Business > WhatsApp > API Setup
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_PHONE_ID     = os.environ.get("WHATSAPP_PHONE_ID", "")
WHATSAPP_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")


def ensure_dirs():
    for d in (UPLOAD_DIR, REVIEW_DIR, ARCHIVE_DIR, OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)

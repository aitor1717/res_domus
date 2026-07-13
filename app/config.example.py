import os
from pathlib import Path

# Copy this file to config.py and fill in your values.
# config.py is gitignored — never commit it.

BASE_DIR = Path(__file__).resolve().parent

# Runtime/data dir — sibling to app/ on the host (repo_root/data); in the
# container this resolves to /data, which the Dockerfile creates and
# docker-compose bind-mounts from ./data.
DATA_DIR = BASE_DIR.parent / "data"

# API key — reads from environment variable first, falls back to the value below.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "sk-ant-...")

# Models — vision receipt parsing needs a strong model; the NL→SQL chat is a
# simpler, schema-constrained task and runs fine (much cheaper) on Haiku.
MODEL_PARSER = os.environ.get("MODEL_PARSER", "claude-sonnet-4-6")
MODEL_CHAT   = os.environ.get("MODEL_CHAT", "claude-haiku-4-5")

# Paths
DB_PATH       = DATA_DIR / "res_domus.db"
AUX_CSV       = BASE_DIR / "aux_items.csv"
UPLOAD_DIR    = DATA_DIR / "input"
REVIEW_DIR    = DATA_DIR / "review"
ARCHIVE_DIR   = DATA_DIR / "archive"
OUTPUT_DIR    = DATA_DIR / "output"
CHAT_LOG_PATH = DATA_DIR / "chat_log.jsonl"

# ntfy.sh push notifications — leave blank to disable
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

# HTTP Basic Auth — leave both blank to disable (not recommended once deployed)
BASIC_AUTH_USER = os.environ.get("BASIC_AUTH_USER", "")
BASIC_AUTH_PASS = os.environ.get("BASIC_AUTH_PASS", "")

# Flask
SECRET_KEY     = os.environ.get("SECRET_KEY", "change-me-in-production")
DEBUG          = os.environ.get("FLASK_DEBUG", "0") == "1"
INSTANCE_LABEL = os.environ.get("INSTANCE_LABEL", "res domus")


def ensure_dirs():
    for d in (UPLOAD_DIR, REVIEW_DIR, ARCHIVE_DIR, OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)

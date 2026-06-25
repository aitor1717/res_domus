import hmac
import logging
import sqlite3
from pathlib import Path

from flask import Flask, render_template, request, Response, send_from_directory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app():
    app = Flask(__name__)

    # Load config
    try:
        import config as cfg
    except ImportError:
        logging.error("config.py not found — copy config.example.py and fill in your API key.")
        raise RuntimeError("config.py not found — copy config.example.py and fill in your API key.")

    cfg.ensure_dirs()

    app.config.update(
        SECRET_KEY=cfg.SECRET_KEY,
        ANTHROPIC_API_KEY=cfg.ANTHROPIC_API_KEY,
        MODEL_PARSER=getattr(cfg, "MODEL_PARSER", "claude-sonnet-4-6"),
        MODEL_CHAT=getattr(cfg, "MODEL_CHAT", "claude-haiku-4-5"),
        DB_PATH=str(cfg.DB_PATH),
        AUX_CSV=str(cfg.AUX_CSV),
        UPLOAD_DIR=str(cfg.UPLOAD_DIR),
        REVIEW_DIR=str(cfg.REVIEW_DIR),
        ARCHIVE_DIR=str(cfg.ARCHIVE_DIR),
        OUTPUT_DIR=str(cfg.OUTPUT_DIR),
        CHAT_LOG_PATH=str(cfg.CHAT_LOG_PATH),
        NTFY_TOPIC=cfg.NTFY_TOPIC,
        BASIC_AUTH_USER=cfg.BASIC_AUTH_USER,
        BASIC_AUTH_PASS=cfg.BASIC_AUTH_PASS,
    )

    # Warn (don't crash, don't create) if the DB is missing or not initialized yet
    db_path = Path(app.config["DB_PATH"])
    has_purchases = False
    if db_path.exists():
        conn = sqlite3.connect(db_path)
        has_purchases = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='purchases'"
        ).fetchone() is not None
        conn.close()
    if not has_purchases:
        logging.warning(
            "Database has no 'purchases' table — run `python3 scripts/init_db.py` "
            "(or scripts/seed_demo_data.py for sample data) to initialize it."
        )

    # Gate every request behind HTTP Basic Auth (skipped if no credentials configured)
    @app.before_request
    def require_auth():
        user = app.config["BASIC_AUTH_USER"]
        pw = app.config["BASIC_AUTH_PASS"]
        if not user or not pw:
            return None

        auth = request.authorization
        valid = (
            auth is not None
            and hmac.compare_digest(auth.username, user)
            and hmac.compare_digest(auth.password, pw)
        )
        if not valid:
            return Response(
                "Authentication required", 401,
                {"WWW-Authenticate": 'Basic realm="Res Domus"'},
            )
        return None

    # Register API blueprints
    from api.dashboard import bp as dashboard_bp
    from api.upload import bp as upload_bp
    from api.chat import bp as chat_bp
    from api.items import bp as items_bp
    from api.register import bp as register_bp
    from api.settings import bp as settings_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(upload_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(items_bp)
    app.register_blueprint(register_bp)
    app.register_blueprint(settings_bp)

    # Page routes
    @app.get("/")
    def dashboard():
        return render_template("dashboard.html")

    @app.get("/upload")
    def upload():
        return render_template("upload.html")

    @app.get("/items")
    def items():
        return render_template("items.html")

    @app.get("/register")
    def register():
        return render_template("register.html")

    @app.get("/settings")
    def settings():
        return render_template("settings.html")

    # Served from root so its scope covers the whole app, not just /static/
    @app.get("/sw.js")
    def service_worker():
        return send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")

    return app

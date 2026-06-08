from flask import Flask, render_template


def create_app():
    app = Flask(__name__)

    # Load config
    try:
        import config as cfg
    except ImportError:
        raise RuntimeError("config.py not found — copy config.example.py and fill in your API key.")

    cfg.ensure_dirs()

    app.config.update(
        SECRET_KEY=cfg.SECRET_KEY,
        ANTHROPIC_API_KEY=cfg.ANTHROPIC_API_KEY,
        DB_PATH=str(cfg.DB_PATH),
        AUX_CSV=str(cfg.AUX_CSV),
        UPLOAD_DIR=str(cfg.UPLOAD_DIR),
        REVIEW_DIR=str(cfg.REVIEW_DIR),
        ARCHIVE_DIR=str(cfg.ARCHIVE_DIR),
        OUTPUT_DIR=str(cfg.OUTPUT_DIR),
        WHATSAPP_VERIFY_TOKEN=cfg.WHATSAPP_VERIFY_TOKEN,
        WHATSAPP_PHONE_ID=cfg.WHATSAPP_PHONE_ID,
        WHATSAPP_ACCESS_TOKEN=cfg.WHATSAPP_ACCESS_TOKEN,
    )

    # Register API blueprints
    from api.dashboard import bp as dashboard_bp
    from api.upload import bp as upload_bp
    from api.chat import bp as chat_bp
    from api.items import bp as items_bp
    from api.settings import bp as settings_bp
    from api.whatsapp import bp as whatsapp_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(upload_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(items_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(whatsapp_bp)

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

    @app.get("/settings")
    def settings():
        return render_template("settings.html")

    return app

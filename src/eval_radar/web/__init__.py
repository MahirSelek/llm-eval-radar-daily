from __future__ import annotations

import secrets
from pathlib import Path

from flask import Flask

from eval_radar.config import get_settings


def create_app() -> Flask:
    settings = get_settings()
    base = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(base / "templates"),
        static_folder=str(base / "static"),
    )
    app.config["SECRET_KEY"] = settings.flask_secret_key or secrets.token_urlsafe(32)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = False

    from eval_radar.web.routes import bp

    app.register_blueprint(bp)
    return app


def main() -> None:
    app = create_app()
    print("Eval Radar dashboard → http://127.0.0.1:8765")
    app.run(host="127.0.0.1", port=8765, debug=True)


if __name__ == "__main__":
    main()

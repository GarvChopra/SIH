"""SAMANVAY: AICTE integrated complaint monitoring. Flask app factory."""
import logging
import os

from flask import Flask, g, redirect, request
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from domain.constants import BRAND
from services import auth_service

_LOG_CONFIGURED = False


def _configure_logging() -> None:
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return
    level = logging.DEBUG if os.environ.get("FLASK_DEBUG") == "1" else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _LOG_CONFIGURED = True


def create_app() -> Flask:
    _configure_logging()
    log = logging.getLogger("samanvay")

    Config.validate()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = Config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    app.config["SESSION_COOKIE_SECURE"] = Config.is_production()
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # TLS is terminated by the host's proxy; trust its scheme/host only.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    from db import pool
    pool.init_db()

    from db import seeds
    seeds.run()

    log.info("SAMANVAY starting: env=%s, db=%s", Config.APP_ENV, pool.STATE["mode"])

    from blueprints.auth import bp as auth_bp
    app.register_blueprint(auth_bp)

    from blueprints.citizen import bp as citizen_bp
    app.register_blueprint(citizen_bp)

    from blueprints.api import bp as api_bp
    app.register_blueprint(api_bp)

    from blueprints.portal import bp as portal_bp
    app.register_blueprint(portal_bp)

    @app.before_request
    def _load_user():
        """Resolve the current user from the access-token cookie, transparently
        renewing it from the refresh-token cookie when it has expired, and ALWAYS
        reconciling against the live DB row so a deactivation or role change
        takes effect on the very next request."""
        g.current_user = None
        g._auth_cookie_action = None

        if request.path.startswith("/static/"):
            return

        access = request.cookies.get("up_access")
        bearer = request.headers.get("Authorization", "")
        if bearer.lower().startswith("bearer "):            # mobile / API clients
            access = bearer[7:].strip()
        username = None
        try:
            if access:
                username = auth_service.decode_access_token(access)["sub"]
        except auth_service.AuthError:
            username = None

        if username is None:
            rtok = request.cookies.get("up_refresh")
            if rtok:
                try:
                    res = auth_service.refresh(rtok)
                    if res.success:
                        username = res.user["username"]
                        g._auth_cookie_action = ("set", res.access_token, res.refresh_token)
                except auth_service.AuthError:
                    username = None

        if username is None:
            if access or request.cookies.get("up_refresh"):
                g._auth_cookie_action = ("clear",)
            return

        from db import users as _users
        rec = _users.get_by_username(username)
        if not rec or not rec.get("is_active", True):
            g._auth_cookie_action = ("clear",)
            return

        g.current_user = {"id": rec["id"], "username": rec["username"],
                          "display_name": rec.get("display_name") or rec["username"],
                          "role": rec["role"],          # authoritative role, from the DB
                          "bureau_id": rec.get("bureau_id"), "region": rec.get("region"),
                          "department": rec.get("department"),
                          "must_change_pin": bool(rec.get("must_change_pin"))}

    @app.after_request
    def _apply_auth_cookie(resp):
        from blueprints.auth import clear_session_cookies, set_session_cookies
        action = g.get("_auth_cookie_action")
        if action and action[0] == "set":
            set_session_cookies(resp, action[1], action[2])
        elif action and action[0] == "clear":
            clear_session_cookies(resp)
        # case data must never be cached by a browser or proxy
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.before_request
    def _enforce_pin_change():
        user = g.get("current_user")
        if not user or not user.get("must_change_pin"):
            return
        allowed = {"/set-pin", "/set-pin/submit", "/logout"}
        if request.path in allowed or request.path.startswith("/static/"):
            return
        return redirect("/set-pin")

    @app.template_filter("when")
    def _when(ts):
        try:
            import time as _t
            return _t.strftime("%d %b %Y, %H:%M", _t.localtime(float(ts)))
        except Exception:
            return "—"

    @app.template_filter("dm")
    def _dm(ts):
        try:
            import time as _t
            return _t.strftime("%d %b", _t.localtime(float(ts)))
        except Exception:
            return "—"

    @app.template_filter("day")
    def _day(ts):
        try:
            import time as _t
            return _t.strftime("%d %b %Y", _t.localtime(float(ts)))
        except Exception:
            return "—"

    @app.context_processor
    def _inject():
        from db import notifications
        from domain.constants import ROLE_LABELS
        from domain.rbac import has_permission
        user = g.get("current_user")
        unread = 0
        if user:
            try:
                unread = notifications.unread_count(user["id"])
            except Exception:  # noqa: BLE001 - chrome must never 500
                unread = 0
        return {
            "current_user": user,
            "BRAND": BRAND,
            "ROLE_LABELS": ROLE_LABELS,
            "unread_notifications": unread,
            "can": (lambda perm: bool(user) and has_permission(user["role"], perm)),
        }

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "db": pool.STATE["mode"], "env": Config.APP_ENV}

    @app.errorhandler(403)
    def _forbidden(_e):
        from flask import render_template
        wants_json = request.path.endswith("/data") or request.accept_mimetypes.best == "application/json"
        if wants_json:
            return {"error": "forbidden"}, 403
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def _missing(_e):
        from flask import render_template
        if request.path.endswith("/data") or request.accept_mimetypes.best == "application/json":
            return {"error": "not found"}, 404
        return render_template("errors/404.html"), 404

    return app


if __name__ == "__main__":
    _app = create_app()
    _app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
             debug=os.environ.get("FLASK_DEBUG") == "1")

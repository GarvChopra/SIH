"""UNIFIX - GL Bajaj campus infrastructure intelligence. Flask app factory."""
import logging
import os

from flask import Flask, g, redirect, request
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from domain.constants import GLB
from services import auth_service

_LOG_CONFIGURED = False


def _configure_logging() -> None:
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return
    level = logging.DEBUG if os.environ.get("FLASK_DEBUG") == "1" else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _LOG_CONFIGURED = True


def create_app() -> Flask:
    _configure_logging()
    log = logging.getLogger("unifix")

    Config.validate()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = Config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
    app.config["SESSION_COOKIE_SECURE"] = Config.is_production()
    # Lax, not Strict: the return leg from accounts.google.com is a cross-site
    # top-level navigation, and a Strict session cookie would be dropped on
    # that redirect, breaking every Google sign-in.
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # Render terminates TLS and forwards over plain HTTP internally. Without this,
    # url_for(..., _external=True) — used to build the Google OAuth redirect_uri —
    # would generate an http:// URL in production and Google would reject it as a
    # redirect_uri mismatch. Only trust the proxy's scheme/host, not x_for, since
    # existing rate-limiting reads request.remote_addr directly.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    from db import pool
    pool.init_db()

    from db import seeds
    seeds.run()

    log.info("UNIFIX starting — env=%s, db=%s", Config.APP_ENV, pool.STATE["mode"])

    from services import storage_service
    storage_service.warn_if_unconfigured()

    from blueprints.public import bp as public_bp
    app.register_blueprint(public_bp)

    from blueprints.auth import bp as auth_bp, init_google_oauth
    app.register_blueprint(auth_bp)
    init_google_oauth(app)

    from blueprints.faculty import bp as faculty_bp
    app.register_blueprint(faculty_bp)

    from blueprints.admin import bp as admin_bp
    app.register_blueprint(admin_bp)

    @app.before_request
    def _load_user():
        """Resolve the current user from the access-token cookie, transparently
        renewing it from the refresh-token cookie when it has expired, and
        ALWAYS reconciling against the live DB row so a deactivation, a role
        change or a PIN reset takes effect on the very next request instead of
        up to 15 minutes later."""
        g.current_user = None
        g._auth_cookie_action = None          # (set|clear, *args) applied in after_request

        if request.path.startswith("/static/"):
            return                            # static assets never need a user

        access = request.cookies.get("up_access")
        username = None
        try:
            if access:
                username = auth_service.decode_access_token(access)["sub"]
        except auth_service.AuthError:
            username = None

        if username is None:                  # missing / expired — try the refresh cookie
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

        g.current_user = {"username": rec["username"],
                          "display_name": rec.get("display_name") or rec["username"],
                          "role": rec["role"],          # authoritative role, from the DB
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
        return resp

    @app.before_request
    def _enforce_pin_change():
        """A user flagged must_change_pin can only reach the set-PIN screen,
        logout, and static assets until they choose a new PIN."""
        user = g.get("current_user")
        if not user or not user.get("must_change_pin"):
            return
        allowed = {"/profile", "/profile/pin", "/logout", "/set-pin", "/set-pin/submit"}
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

    @app.context_processor
    def _inject():
        from domain.rbac import has_permission
        user = g.get("current_user")
        bell_dot = False
        if user and user["role"] == "reporter":
            try:
                import time as _t
                from db import grievances as _gr, users as _u
                rec = _u.get_by_username(user["username"])
                if rec:
                    cut = _t.time() - 72 * 3600
                    bell_dot = any((r["updated_at"] or 0) >= cut and r["status"] != "reported"
                                   for r in _gr.list_for_reporter(rec["id"]))
            except Exception:
                bell_dot = False
        return {
            "current_user": user,
            "GLB": GLB,
            "bell_dot": bell_dot,
            "can": (lambda perm: bool(user) and has_permission(user["role"], perm)),
        }

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "db": pool.STATE["mode"], "env": Config.APP_ENV}

    @app.errorhandler(403)
    def _forbidden(_e):
        """A recoverable page instead of Werkzeug's bare "Forbidden" text.

        This is what a faculty account sees on an /admin URL, and — historically
        — what an installed PWA could get stuck on when a service worker had
        cached an old 403. The page always offers a way back and can reset the
        app's cached data itself."""
        from flask import render_template, request
        user = g.get("current_user")
        wants_json = request.path.endswith("/data") or request.path.startswith("/api/") \
            or request.accept_mimetypes.best == "application/json"
        if wants_json:
            return {"error": "forbidden"}, 403
        return render_template("errors/403.html", user=user,
                               admin_area=request.path.startswith("/admin")), 403

    return app


if __name__ == "__main__":
    _app = create_app()
    _app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
             debug=os.environ.get("FLASK_DEBUG") == "1")

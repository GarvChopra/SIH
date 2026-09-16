"""Auth blueprint: login / logout / token refresh / forced PIN change."""
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.flask_client import OAuth
from flask import Blueprint, abort, g, make_response, redirect, render_template, request, url_for

from config import Config
from services import auth_service

bp = Blueprint("auth", __name__, template_folder="../../templates")

oauth = OAuth()


def init_google_oauth(app):
    """Register Google as an OAuth provider. No-op when credentials aren't
    configured — google_login() below then serves the dev-only mock picker
    instead of a real Google redirect."""
    if not Config.google_oauth_configured():
        return
    oauth.init_app(app)
    oauth.register(
        name="google",
        client_id=Config.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=Config.GOOGLE_OAUTH_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

_ACCESS, _REFRESH = "up_access", "up_refresh"


def _secure_cookies() -> bool:
    # Evaluated per-request (not import time) so an omitted APP_ENV can't silently
    # leave Secure off after the process is already running.
    return Config.is_production()


# The refresh cookie is sent on every request (path "/") so the server-side
# silent-refresh in app.py can read it; it is still HttpOnly + SameSite=Strict.
_REFRESH_PATH = "/"


def set_session_cookies(resp, access: str, refresh: str, remember: bool = False):
    secure = _secure_cookies()
    resp.set_cookie(_ACCESS, access, max_age=15 * 60, httponly=True,
                    samesite="Strict", secure=secure, path="/")
    resp.set_cookie(_REFRESH, refresh, max_age=(30 if remember else 7) * 24 * 3600,
                    httponly=True, samesite="Strict", secure=secure, path=_REFRESH_PATH)
    return resp


def clear_session_cookies(resp):
    resp.set_cookie(_ACCESS, "", expires=0, path="/")
    resp.set_cookie(_REFRESH, "", expires=0, path=_REFRESH_PATH)
    resp.set_cookie(_REFRESH, "", expires=0, path="/auth/refresh")   # legacy path
    return resp


# backwards-compatible aliases (used within this blueprint)
_set_cookies = set_session_cookies
_clear_cookies = clear_session_cookies


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if g.get("current_user"):
            return redirect("/admin" if g.current_user["role"] == "admin" else "/")
        return render_template("login.html", error=None)

    username = (request.form.get("username") or "").strip()
    pin = (request.form.get("pin") or "").strip()
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    if not auth_service.check_rate_limit(f"{username.lower()}@{ip}"):
        return render_template("login.html", error="Too many attempts - wait a few minutes."), 429

    result = auth_service.login(username, pin)
    if not result.success:
        return render_template("login.html", error=result.error), 401

    remember = bool(request.form.get("remember"))
    if remember:
        result.refresh_token = auth_service.create_refresh_token(result.user["username"], days=30)

    if result.user.get("must_change_pin"):
        target = "/set-pin"
    else:
        target = "/admin" if result.user["role"] == "admin" else "/"
    resp = make_response(redirect(target))
    return _set_cookies(resp, result.access_token, result.refresh_token, remember=remember)


@bp.get("/logout")
def logout():
    return _clear_cookies(make_response(redirect(url_for("auth.login"))))


@bp.get("/set-pin")
def set_pin_page():
    if not g.get("current_user"):
        return redirect("/login")
    return render_template("set_pin.html", error=request.args.get("err"))


@bp.post("/set-pin/submit")
def set_pin_submit():
    if not g.get("current_user"):
        return redirect("/login")
    from db import users
    rec = users.get_by_username(g.current_user["username"])
    if not rec:
        return redirect("/login")
    new = (request.form.get("new") or "").strip()
    confirm = (request.form.get("confirm") or "").strip()
    if not (new.isdigit() and 4 <= len(new) <= 8):
        return redirect("/set-pin?err=PIN+must+be+4-8+digits")
    if new != confirm:
        return redirect("/set-pin?err=PINs+do+not+match")
    if auth_service.verify_pin(new, rec["pin_hash"]):
        return redirect("/set-pin?err=Choose+a+PIN+different+from+the+temporary+one")
    users.set_pin(rec["id"], auth_service.hash_pin(new))
    from db import audit
    audit.add(rec["username"], "user.set_pin", target_type="user", target_id=rec["id"])
    return redirect("/admin" if rec["role"] == "admin" else "/")


@bp.post("/auth/refresh")
def refresh():
    tok = request.cookies.get(_REFRESH)
    if not tok:
        return {"error": "no refresh token"}, 401
    try:
        result = auth_service.refresh(tok)
    except auth_service.AuthError as e:
        return _clear_cookies(make_response({"error": str(e)}, 401))
    if not result.success:
        return _clear_cookies(make_response({"error": result.error}, 401))
    resp = make_response({"ok": True, "role": result.user["role"]})
    return _set_cookies(resp, result.access_token, result.refresh_token)


def _finish_google_login(email: str, name: str):
    result = auth_service.login_with_google(email, name)
    if not result.success:
        return render_template("login.html", error=result.error), 401
    resp = make_response(redirect("/"))
    return _set_cookies(resp, result.access_token, result.refresh_token, remember=True)


@bp.get("/auth/google/login")
def google_login():
    if g.get("current_user"):
        return redirect("/admin" if g.current_user["role"] == "admin" else "/")
    if not Config.google_oauth_configured():
        if Config.is_production() or Config.db_is_remote():
            abort(404)
        return render_template("auth/google_mock.html")
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


_GOOGLE_CANCELLED_ERROR = "Google sign-in was cancelled or expired. Please try again."


@bp.get("/auth/google/callback")
def google_callback():
    if not Config.google_oauth_configured():
        abort(404)
    try:
        token = oauth.google.authorize_access_token()
    except OAuthError:
        # Covers both failure modes: the user declined consent on Google's screen
        # (OAuthError with error=access_denied), and a missing/stale Flask session
        # so the OAuth state/nonce can't be matched (MismatchingStateError, a
        # subclass of OAuthError) — e.g. the cookie expired while the user sat on
        # Google's consent screen.
        return render_template("login.html", error=_GOOGLE_CANCELLED_ERROR), 401
    userinfo = token.get("userinfo") or {}
    if not userinfo.get("email_verified"):
        return render_template(
            "login.html",
            error="Your Google account's email address isn't verified. "
                  "Please verify it with Google and try again.",
        ), 401
    return _finish_google_login(userinfo.get("email"), userinfo.get("name"))


@bp.post("/auth/google/mock-callback")
def google_mock_callback():
    if Config.is_production() or Config.google_oauth_configured() or Config.db_is_remote():
        abort(404)
    return _finish_google_login(request.form.get("email"), request.form.get("name"))

"""Auth blueprint: login / logout / token refresh / forced PIN change."""
from flask import Blueprint, g, make_response, redirect, render_template, request, url_for

from config import Config
from services import auth_service

bp = Blueprint("auth", __name__, template_folder="../../templates")

_ACCESS, _REFRESH = "up_access", "up_refresh"


def _secure_cookies() -> bool:
    # Evaluated per-request (not import time) so an omitted APP_ENV can't silently
    # leave Secure off after the process is already running.
    return Config.is_production()


# The refresh cookie is sent on every request (path "/") so the server-side
# silent-refresh in app.py can read it; it is still HttpOnly + SameSite=Strict.
_REFRESH_PATH = "/"


def set_session_cookies(resp, access: str, refresh: str, remember: bool = False,
                        samesite: str = "Strict"):
    secure = _secure_cookies()
    resp.set_cookie(_ACCESS, access, max_age=15 * 60, httponly=True,
                    samesite=samesite, secure=secure, path="/")
    resp.set_cookie(_REFRESH, refresh, max_age=(30 if remember else 7) * 24 * 3600,
                    httponly=True, samesite=samesite, secure=secure, path=_REFRESH_PATH)
    return resp


def clear_session_cookies(resp):
    resp.set_cookie(_ACCESS, "", expires=0, path="/")
    resp.set_cookie(_REFRESH, "", expires=0, path=_REFRESH_PATH)
    return resp


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if g.get("current_user"):
            return redirect("/")
        return render_template("login.html", error=None, demo=_demo_accounts())

    username = (request.form.get("username") or "").strip()
    pin = (request.form.get("pin") or "").strip()
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    if not auth_service.check_rate_limit(f"{username.lower()}@{ip}"):
        return render_template("login.html", error="Too many attempts. Wait a few minutes.",
                               demo=_demo_accounts()), 429

    result = auth_service.login(username, pin)
    if not result.success:
        return render_template("login.html", error=result.error, demo=_demo_accounts()), 401

    remember = bool(request.form.get("remember"))
    if remember:
        result.refresh_token = auth_service.create_refresh_token(result.user["username"], days=30)

    target = "/set-pin" if result.user.get("must_change_pin") else "/"
    resp = make_response(redirect(target))
    return set_session_cookies(resp, result.access_token, result.refresh_token, remember=remember)


def _demo_accounts():
    """Shown on the login screen in development only, never in production."""
    if Config.is_production():
        return []
    from db import seeds as seeds_demo
    return seeds_demo.DEMO_ACCOUNTS if seeds_demo.demo_loaded() else []


@bp.get("/logout")
def logout():
    return clear_session_cookies(make_response(redirect(url_for("auth.login"))))


@bp.get("/set-pin")
def set_pin_page():
    if not g.get("current_user"):
        return redirect("/login")
    return render_template("set_pin.html", error=request.args.get("err"))


@bp.post("/set-pin/submit")
def set_pin_submit():
    if not g.get("current_user"):
        return redirect("/login")
    from db import audit, users
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
    audit.add(rec["username"], "user.set_pin", target_type="user", target_id=rec["id"])
    return redirect("/")


@bp.post("/auth/refresh")
def refresh():
    tok = request.cookies.get(_REFRESH)
    if not tok:
        return {"error": "no refresh token"}, 401
    try:
        result = auth_service.refresh(tok)
    except auth_service.AuthError as e:
        return clear_session_cookies(make_response({"error": str(e)}, 401))
    if not result.success:
        return clear_session_cookies(make_response({"error": result.error}, 401))
    resp = make_response({"ok": True, "role": result.user["role"]})
    return set_session_cookies(resp, result.access_token, result.refresh_token)

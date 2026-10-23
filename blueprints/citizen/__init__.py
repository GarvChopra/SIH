"""Citizen portal: register, file a complaint, track it. Same backend as the officials."""
from flask import (Blueprint, abort, flash, g, jsonify, make_response, redirect,
                   render_template, request)

from db import notifications
from domain.constants import CATEGORIES
from services import auth_service, citizen_service, complaint_service, file_service
from services.errors import ServiceError

bp = Blueprint("citizen", __name__, template_folder="../../templates")


def _me():
    return g.current_user


@bp.before_request
def _guard():
    if request.endpoint == "citizen.register":
        return None
    user = g.get("current_user")
    if not user:
        if request.path.endswith("/search"):
            return jsonify({"error": "unauthenticated"}), 401
        return redirect("/login")
    if user["role"] != "citizen":
        return redirect("/")


@bp.context_processor
def _ctx():
    return {"c_unread": notifications.unread_count(_me()["id"]) if g.get("current_user") else 0}


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.get("current_user"):
        return redirect("/")
    f = request.form
    if request.method == "POST":
        try:
            if f.get("password") != f.get("confirm"):
                raise ServiceError("the two passwords do not match")
            u = citizen_service.register(f.get("username"), f.get("display_name"),
                                         f.get("password"), f.get("contact"))
            res = auth_service.login(u["username"], f.get("password"))
            from blueprints.auth import set_session_cookies
            resp = make_response(redirect("/my"))
            flash("Welcome. Your account is ready; you can file a complaint now.", "ok")
            return set_session_cookies(resp, res.access_token, res.refresh_token)
        except ServiceError as e:
            flash(str(e), "err")
    return render_template("register.html", f=f)


@bp.get("/my")
def home():
    rows = citizen_service.list_own(_me())
    open_n = sum(1 for r in rows if r["status"] != "Disposed")
    return render_template("citizen_home.html", rows=rows, open_n=open_n,
                           closed_n=len(rows) - open_n,
                           notes=notifications.for_user(_me()["id"], limit=5))


@bp.get("/my/institutions/search")
def institution_search():
    return jsonify(citizen_service.institution_search(request.args.get("q", "")))


@bp.route("/my/new", methods=["GET", "POST"])
def new():
    f = request.form
    picked = None
    if request.method == "POST":
        from db import institutions
        picked = institutions.get_by_aicte_id(f.get("aicte_id"))
        try:
            c = complaint_service.create_for_citizen(
                _me(), aicte_id=f.get("aicte_id"), category=f.get("category"),
                title=f.get("title"), description=f.get("description"))
            for fs in [x for x in request.files.getlist("files") if x and x.filename][
                    :file_service.MAX_FILES]:
                try:
                    name, stored = file_service.save(fs)
                    citizen_service.add_evidence(_me(), c["case_number"], name, stored)
                except ServiceError as e:
                    flash(f"Complaint filed, but a file was skipped: {e}", "err")
            return redirect(f"/my/c/{c['case_number']}?new=1")
        except ServiceError as e:
            flash(str(e), "err")
    return render_template("citizen_new.html", categories=CATEGORIES, f=f, picked=picked)


@bp.get("/my/c/<case_number>")
def case(case_number):
    try:
        d = citizen_service.detail(_me(), case_number)
    except ServiceError:
        abort(404)
    return render_template("citizen_case.html", d=d, is_new=request.args.get("new") == "1")


@bp.post("/my/c/<case_number>/evidence")
def add_evidence(case_number):
    files = [x for x in request.files.getlist("files") if x and x.filename]
    if not files:
        flash("Choose a file to attach.", "err")
        return redirect(f"/my/c/{case_number}")
    try:
        for fs in files[:file_service.MAX_FILES]:
            name, stored = file_service.save(fs)
            citizen_service.add_evidence(_me(), case_number, name, stored)
        flash("Evidence added to your complaint.", "ok")
    except ServiceError as e:
        if e.status_code == 404:
            abort(404)
        flash(str(e), "err")
    return redirect(f"/my/c/{case_number}")


@bp.get("/my/notifications")
def notification_list():
    from db import complaints
    nums = {c["id"]: c["case_number"] for c in complaints.all_rows()
            if c.get("citizen_id") == _me()["id"]}
    rows = notifications.for_user(_me()["id"])
    for r in rows:
        r["case_number"] = nums.get(r["complaint_id"])
    return render_template("citizen_notifications.html", rows=rows)


@bp.post("/my/notifications/read")
def notification_read():
    nid = request.form.get("id")
    notifications.mark_read(_me()["id"], int(nid) if (nid or "").isdigit() else None)
    return redirect("/my/notifications")

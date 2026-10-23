"""Citizen JSON API (v1) for native/mobile clients.

Same services as the citizen web pages, so web and mobile can never disagree
about what a complainant may see. Auth is a bearer access token (15 min) plus a
refresh token; the web UI's cookie session also works for same-origin calls.
Every endpoint is citizen-only: officials use the portal, not this API.
"""
from flask import Blueprint, g, jsonify, request

from db import complaints, notifications
from domain.constants import CATEGORIES
from services import auth_service, citizen_service, complaint_service, file_service
from services.errors import ServiceError

bp = Blueprint("api", __name__, url_prefix="/api/v1")


@bp.errorhandler(ServiceError)
def _service_error(e):
    return jsonify({"error": str(e)}), e.status_code


@bp.errorhandler(413)
def _too_big(_e):
    return jsonify({"error": "upload too large"}), 413


def _user_json(u):
    return {"id": u["id"], "username": u["username"], "name": u["display_name"], "role": u["role"]}


def _need_citizen():
    u = g.get("current_user")
    if not u:
        return None, (jsonify({"error": "authentication required"}), 401)
    if u["role"] != "citizen":
        return None, (jsonify({"error": "this API is for complainants"}), 403)
    return u, None


def _tokens(result):
    return {"access_token": result.access_token, "refresh_token": result.refresh_token,
            "token_type": "Bearer", "expires_in": 15 * 60, "user": _user_json(result.user)}


@bp.post("/auth/register")
def register():
    b = request.get_json(silent=True) or {}
    u = citizen_service.register(b.get("username"), b.get("name"), b.get("password"), b.get("email"))
    res = auth_service.login(u["username"], b.get("password"))
    return jsonify(_tokens(res)), 201


@bp.post("/auth/login")
def login():
    b = request.get_json(silent=True) or {}
    key = f"{(b.get('username') or '').lower()}@{request.remote_addr}"
    if not auth_service.check_rate_limit(key):
        return jsonify({"error": "too many attempts, wait a few minutes"}), 429
    res = auth_service.login(b.get("username") or "", b.get("password") or "")
    if not res.success:
        return jsonify({"error": res.error}), 401
    if res.user["role"] != "citizen":
        return jsonify({"error": "this API is for complainants; officials use the portal"}), 403
    return jsonify(_tokens(res))


@bp.post("/auth/refresh")
def refresh():
    b = request.get_json(silent=True) or {}
    try:
        res = auth_service.refresh(b.get("refresh_token") or "")
    except auth_service.AuthError as e:
        return jsonify({"error": str(e)}), 401
    if not res.success or res.user["role"] != "citizen":
        return jsonify({"error": "cannot refresh"}), 401
    return jsonify(_tokens(res))


@bp.get("/me")
def me():
    u, err = _need_citizen()
    return err or jsonify(_user_json(u))


@bp.get("/institutions/search")
def institutions():
    u, err = _need_citizen()
    return err or jsonify({"results": citizen_service.institution_search(request.args.get("q", ""))})


@bp.get("/categories")
def categories():
    u, err = _need_citizen()
    return err or jsonify({"categories": CATEGORIES,
                           "types": [{"id": "complaint", "label": "Report a problem"},
                                     {"id": "query", "label": "Ask a question"}]})


@bp.get("/complaints")
def list_complaints():
    u, err = _need_citizen()
    return err or jsonify({"complaints": citizen_service.list_own(u)})


@bp.post("/complaints")
def create_complaint():
    u, err = _need_citizen()
    if err:
        return err
    b = request.get_json(silent=True) or {}
    c = complaint_service.create_for_citizen(
        u, aicte_id=b.get("aicte_id"), category=b.get("category"),
        tier=1 if str(b.get("type", "")).lower() == "query" else 2,
        title=b.get("title"), description=b.get("description"))
    return jsonify({"tracking_id": c["case_number"],
                    "complaint": citizen_service.detail(u, c["case_number"])["case"]}), 201


@bp.get("/complaints/<case_number>")
def get_complaint(case_number):
    u, err = _need_citizen()
    return err or jsonify(citizen_service.detail(u, case_number))


@bp.post("/complaints/<case_number>/evidence")
def add_evidence(case_number):
    u, err = _need_citizen()
    if err:
        return err
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        return jsonify({"error": "attach at least one file"}), 400
    added = []
    for fs in files[:file_service.MAX_FILES]:
        name, stored = file_service.save(fs)
        doc = citizen_service.add_evidence(u, case_number, name, stored)
        added.append({"id": doc["id"], "filename": name})
    return jsonify({"added": added}), 201


@bp.get("/notifications")
def list_notifications():
    u, err = _need_citizen()
    if err:
        return err
    nums = {c["id"]: c["case_number"] for c in complaints.all_rows()
            if c.get("citizen_id") == u["id"]}
    rows = [{"id": n["id"], "message": n["message"], "type": n["type"], "at": n["created_at"],
             "read": n["read_at"] is not None, "tracking_id": nums.get(n["complaint_id"])}
            for n in notifications.for_user(u["id"])]
    return jsonify({"unread": sum(1 for r in rows if not r["read"]), "notifications": rows})


@bp.post("/notifications/read")
def read_notifications():
    u, err = _need_citizen()
    if err:
        return err
    nid = (request.get_json(silent=True) or {}).get("id")
    notifications.mark_read(u["id"], int(nid) if str(nid or "").isdigit() else None)
    return jsonify({"ok": True})

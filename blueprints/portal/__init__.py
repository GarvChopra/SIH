"""Official portal: cases, institutions, overlap review, notifications, reports, admin."""
import time

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file)

from db import audit, bureaus, case_actions, documents, institutions, notifications, users
from domain.constants import (ALL_CATEGORIES, CATEGORIES, PRIORITIES, REGIONS, ROLE_LABELS, STAFF_ROLES,
                              STATUSES, SUPER_ADMIN)
from domain import rbac
from services import (access_service, case_view_service, citizen_service, complaint_service,
                      dashboard_service, file_service, institution_service, overlap_service,
                      report_service, sla_service)
from services.auth_service import hash_pin
from services.errors import ServiceError

bp = Blueprint("portal", __name__, template_folder="../../templates")

_OPEN_TO_CITIZENS = {"portal.download"}


@bp.before_request
def _guard():
    user = g.get("current_user")
    if not user:
        if request.path.endswith("/data"):
            return jsonify({"error": "unauthenticated"}), 401
        return redirect("/login")
    if user["role"] == "citizen" and request.endpoint not in _OPEN_TO_CITIZENS:
        return redirect("/my")


@bp.context_processor
def _shell_context():
    user = g.get("current_user")
    out = {"nav_counts": {"overlaps": 0, "aliases": 0}, "my_bureau": None}
    if not user or user["role"] == "citizen":
        return out
    out["my_bureau"] = bureaus.get(user.get("bureau_id")) if user.get("bureau_id") else None
    try:
        if rbac.has_permission(user["role"], rbac.ALIAS_REVIEW):
            out["nav_counts"]["aliases"] = len({r["complaint"]["id"] for r in
                                                   institution_service.pending_suggestions(user)})
        from services import overlap_service
        out["nav_counts"]["overlaps"] = len(overlap_service.review_queue(user))
    except Exception:  # noqa: BLE001 - page chrome must never 500
        pass
    return out


def _user():
    return g.current_user


def _do(fn, *args, ok, back, **kw):
    """Run a service action; flash its outcome; always redirect (POST/redirect/GET)."""
    try:
        fn(*args, **kw)
        flash(ok, "ok")
    except ServiceError as e:
        flash(str(e), "err")
    return redirect(back)


def _date(s, end=False):
    try:
        t = time.mktime(time.strptime(s, "%Y-%m-%d"))
        return t + 86399 if end else t
    except (TypeError, ValueError):
        return None


def _filters():
    a = request.args
    f = {k: (a.get(k) or "").strip() for k in case_view_service.FILTER_KEYS}
    f["date_from"] = _date(f["date_from"])
    f["date_to"] = _date(a.get("date_to"), end=True)
    f["overlap"] = f["overlap"] in ("1", "true", "yes")
    return f


@bp.get("/")
def dashboard():
    try:
        sla_service.run_sweep()          # idempotent: one due-soon/overdue notice per case
    except Exception:  # noqa: BLE001 - never block the dashboard
        pass
    return render_template("dashboard.html", d=dashboard_service.build(_user()))


@bp.get("/dashboard/data")
def dashboard_data():
    d = dashboard_service.build(_user())
    d.pop("alert_rows", None)
    d.pop("needs_attention", None)
    d["bureau_rows"] = [{"bureau": r["bureau"]["code"], **{k: v for k, v in r.items() if k != "bureau"}}
                        for r in d["bureau_rows"]]
    d["top_institutions"] = [{"aicte_id": r["institution"]["aicte_permanent_id"],
                              "name": r["institution"]["name"], "active": r["active"],
                              "bureaus": r["bureaus"]} for r in d["top_institutions"]]
    return jsonify(d)


@bp.get("/reports")
def reports():
    _need(rbac.REPORT_VIEW)
    return render_template("reports.html", reports=report_service.REPORTS)


@bp.get("/reports/<name>")
def report_view(name):
    _need(rbac.REPORT_VIEW)
    try:
        r = report_service.build(name, _user())
    except KeyError:
        abort(404)
    return render_template("report_view.html", r=r, reports=report_service.REPORTS)


@bp.get("/reports/<name>.csv")
def report_csv(name):
    _need(rbac.REPORT_VIEW)
    try:
        r = report_service.build(name, _user())
    except KeyError:
        abort(404)
    from flask import Response
    audit.add(_user()["username"], "report.export", target_type="report", target_id=name)
    return Response(report_service.to_csv(r), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=samanvay-{name}.csv"})


# ── cases ─────────────────────────────────────────────────────────────────

@bp.get("/cases")
def cases():
    f = _filters()
    rows = case_view_service.list_cases(_user(), f)
    from urllib.parse import urlencode
    view = "board" if request.args.get("view") == "board" else "list"
    group = "tier" if request.args.get("group") == "tier" else "status"
    qs = urlencode([(k, v) for k, v in request.args.items(multi=True)
                    if k not in ("view", "group") and v])
    from domain.constants import TIER_INFO
    return render_template("cases.html", rows=rows, f=request.args, view=view, qs=qs, group=group,
                           tier_info=TIER_INFO,
                           board=_board(_user(), rows, group) if view == "board" else None,
                           bureaus=bureaus.list_all(),
                           categories=ALL_CATEGORIES, statuses=STATUSES, regions=REGIONS,
                           officers=[u for u in users.list_staff()
                                     if u["role"] in ("officer", "regional_user")])


BOARD_LIMIT = 30


def _moves(user, r):
    """Statuses this user may drag the card to. Only a hint for the UI: the service
    layer re-checks every rule (and writes the audit/history entry) on submit."""
    from db import complaints as _cm
    from domain.constants import STATUS_TRANSITIONS, TRANSFERRED as _T
    c = _cm.get(r["id"])
    if not c:
        return []
    out = []
    for t in STATUS_TRANSITIONS.get(c["status"], []):
        if t == "Pending":                        # accepting a transfer
            if c["status"] == _T and access_service.can_manage_bureau(user, c["bureau_id"]) \
                    and rbac.has_permission(user["role"], rbac.COMPLAINT_ESCALATE):
                out.append(t)
        elif t in ("Transferred", "Escalated"):
            if access_service.can_edit(user, c) and rbac.has_permission(user["role"], rbac.COMPLAINT_ESCALATE):
                out.append(t)
        elif access_service.can_edit(user, c) and rbac.has_permission(user["role"], rbac.COMPLAINT_UPDATE):
            out.append(t)
    return out


def _columns(user, rows, limit):
    """Kanban columns: one per status, capped so the page stays light."""
    cols = []
    for s in STATUSES:
        mine = [r for r in rows if r["status"] == s]
        cards = mine[:limit]
        for r in cards:
            r["moves"] = _moves(user, r)
        cols.append({"status": s, "total": len(mine), "cards": cards,
                     "more": max(0, len(mine) - limit)})
    return cols


def _board(user, rows, group="status"):
    """Board lanes. group="status": one lane of status columns. group="tier": one lane per
    tier (Query / Problem / Escalated), each with its own status columns."""
    from domain.constants import TIER_INFO, TIERS
    if group == "tier":
        lanes = []
        for t in TIERS:
            mine = [r for r in rows if r.get("tier") == t]
            lanes.append({"key": t, "info": TIER_INFO[t], "total": len(mine),
                          "columns": _columns(user, mine, 10)})
        return lanes
    return [{"key": None, "info": None, "total": len(rows),
             "columns": _columns(user, rows, BOARD_LIMIT)}]


def _safe_next(url):
    """Only ever redirect back into the case pages (no open redirect)."""
    if url and url.startswith("/cases") and not url.startswith("//") and "\n" not in url:
        return url
    return None


@bp.get("/cases/data")
def cases_data():
    rows = case_view_service.list_cases(_user(), _filters())
    keep = ("case_number", "title", "status", "category", "bureau_code", "aicte_id",
            "institution_label", "sla", "overlap", "restricted", "received_at")
    return jsonify({"count": len(rows), "cases": [{k: r[k] for k in keep} for r in rows]})


@bp.route("/cases/new", methods=["GET", "POST"])
def case_new():
    user = _user()
    if not rbac.has_permission(user["role"], rbac.COMPLAINT_CREATE):
        abort(403)
    if request.method == "POST":
        f = request.form
        try:
            c = complaint_service.create(
                user, title=f.get("title"), description=f.get("description"),
                category=f.get("category"), tier=f.get("tier") or 2,
                priority=f.get("priority") or "medium",
                aicte_id=f.get("aicte_id"), institution_name=f.get("institution_name"),
                bureau_id=int(f["bureau_id"]) if (f.get("bureau_id") or "").isdigit() else None)
            flash(f"Case {c['case_number']} registered.", "ok")
            return redirect(f"/cases/{c['case_number']}")
        except ServiceError as e:
            flash(str(e), "err")
    return render_template("case_new.html", categories=CATEGORIES, priorities=PRIORITIES,
                           institutions=institutions.list_all(), bureaus=bureaus.list_all(),
                           f=request.form)


@bp.get("/cases/<case_number>")
def case_detail(case_number):
    try:
        d = case_view_service.case_detail(_user(), case_number)
    except ServiceError:
        abort(404)
    return render_template("case_detail.html", d=d)


@bp.post("/cases/<case_number>/<action>")
def case_action(case_number, action):
    user, f = _user(), request.form
    back = _safe_next(f.get("next")) or f"/cases/{case_number}"
    if action == "assign":
        return _do(complaint_service.assign, user, case_number, int(f.get("officer_id") or 0),
                   ok="Officer assigned.", back=back)
    if action == "status":
        return _do(complaint_service.change_status, user, case_number, f.get("to"),
                   f.get("remarks"), f.get("citizen_message"), ok="Status updated.", back=back)
    if action == "transfer":
        return _do(complaint_service.transfer, user, case_number, int(f.get("to_bureau") or 0),
                   f.get("remarks"), ok="Case transferred; the receiving bureau must accept it.",
                   back=back)
    if action == "accept":
        return _do(complaint_service.accept_transfer, user, case_number,
                   ok="Transfer accepted.", back=back)
    if action == "escalate":
        return _do(complaint_service.escalate, user, case_number, f.get("remarks"),
                   ok="Case escalated.", back=back)
    if action == "request":
        return _do(complaint_service.request_action, user, case_number, f.get("kind"),
                   f.get("remarks"), ok="Request sent to your bureau admin.", back=back)
    if action == "remark":
        return _do(complaint_service.add_remark, user, case_number, f.get("remarks"),
                   ok="Remark added.", back=back)
    if action == "citizen-update":
        return _do(complaint_service.post_citizen_update, user, case_number, f.get("message"),
                   ok="Message sent to the complainant.", back=back)
    if action == "edit":
        return _do(complaint_service.update_text, user, case_number, f.get("title"),
                   f.get("description"), ok="Case text updated; overlaps re-checked.", back=back)
    if action == "document":
        files = [x for x in request.files.getlist("files") if x and x.filename]
        if not files:
            flash("Choose a file to attach.", "err")
            return redirect(back)
        try:
            for fs in files[:file_service.MAX_FILES]:
                name, stored = file_service.save(fs)
                complaint_service.add_document(user, case_number, name, stored)
            flash("Document attached.", "ok")
        except ServiceError as e:
            flash(str(e), "err")
        return redirect(back)
    abort(404)


@bp.get("/files/<int:doc_id>")
def download(doc_id):
    doc = documents.get(doc_id)
    if not doc or not citizen_service.can_download(_user(), doc):
        abort(404)
    path = file_service.path_for(doc["stored_name"])
    try:
        return send_file(path, as_attachment=True, download_name=doc["filename"])
    except FileNotFoundError:
        abort(404)


# ── institutions ──────────────────────────────────────────────────────────

@bp.get("/institutions")
def institution_list():
    q = (request.args.get("q") or "").strip().lower()
    region = request.args.get("region") or ""
    rows = []
    for i in institutions.list_all():
        if q and q not in i["name"].lower() and q not in i["aicte_permanent_id"].lower():
            continue
        if region and i["region"] != region:
            continue
        rows.append(i)
    from db import complaints as _c
    by_inst = {}
    for c in _c.all_rows():
        if c["institution_id"]:
            slot = by_inst.setdefault(c["institution_id"], {"active": 0, "total": 0, "bureaus": set()})
            slot["total"] += 1
            if c["status"] != "Disposed":
                slot["active"] += 1
                slot["bureaus"].add(c["bureau_id"])
    stats = {i["id"]: by_inst.get(i["id"], {"active": 0, "total": 0, "bureaus": set()}) for i in rows}
    return render_template("institutions.html", rows=rows, stats=stats, regions=REGIONS,
                           q=request.args.get("q", ""), region=region)


@bp.post("/institutions")
def institution_create():
    f = request.form
    try:
        inst = institution_service.create_institution(
            _user(), f.get("aicte_id"), f.get("name"), f.get("address"), f.get("region") or None)
        flash(f"{inst['name']} added to the registry.", "ok")
        return redirect(f"/institutions/{inst['aicte_permanent_id']}")
    except ServiceError as e:
        flash(str(e), "err")
        return redirect("/institutions")


@bp.get("/institutions/aliases")
def alias_list():
    if not rbac.has_permission(_user()["role"], rbac.ALIAS_REVIEW):
        abort(403)
    return render_template("aliases.html", rows=institution_service.pending_suggestions(_user()))


@bp.post("/institutions/aliases/<int:alias_id>/<decision>")
def alias_decide(alias_id, decision):
    if decision == "approve":
        return _do(institution_service.approve_alias, _user(), alias_id,
                   ok="Name approved; the case is now linked to the institution.",
                   back="/institutions/aliases")
    if decision == "reject":
        return _do(institution_service.reject_alias, _user(), alias_id,
                   ok="Suggestion rejected.", back="/institutions/aliases")
    abort(404)


@bp.get("/institutions/<aicte_id>")
def institution_profile(aicte_id):
    try:
        d = case_view_service.institution_profile(_user(), aicte_id)
    except ServiceError:
        abort(404)
    return render_template("institution_profile.html", d=d)


# ── overlap review ────────────────────────────────────────────────────────

@bp.get("/overlaps")
def overlap_list():
    tab = "decided" if request.args.get("tab") == "decided" else "pending"
    rows = overlap_service.review_queue(_user(), include_decided=(tab == "decided"))
    return render_template("overlaps.html", rows=rows, tab=tab)


@bp.get("/overlaps/<int:link_id>")
def overlap_detail(link_id):
    try:
        d = overlap_service.review_detail(_user(), link_id)
    except ServiceError:
        abort(404)
    return render_template("overlap_detail.html", d=d)


@bp.post("/overlaps/<int:link_id>/decide")
def overlap_decide(link_id):
    f = request.form
    return _do(overlap_service.decide, _user(), link_id, f.get("decision"), f.get("reason"),
               ok="Decision recorded in both cases' histories.", back=f"/overlaps/{link_id}")


# ── notifications ─────────────────────────────────────────────────────────

@bp.get("/notifications")
def notification_list():
    rows = notifications.for_user(_user()["id"])
    from db import complaints as _c
    cases = {c["id"]: c["case_number"] for c in _c.all_rows()}
    for r in rows:
        r["case_number"] = cases.get(r["complaint_id"])
    return render_template("notifications.html", rows=rows)


@bp.post("/notifications/read")
def notification_read():
    nid = request.form.get("id")
    notifications.mark_read(_user()["id"], int(nid) if (nid or "").isdigit() else None)
    return redirect(request.form.get("next") or "/notifications")


# ── administration (super admin) ──────────────────────────────────────────

def _need(perm):
    if not rbac.has_permission(_user()["role"], perm):
        abort(403)


@bp.get("/admin/users")
def admin_users():
    _need(rbac.USER_MANAGE)
    return render_template("admin_users.html", rows=users.list_staff(), bureaus=bureaus.list_all(),
                           bmap={b["id"]: b for b in bureaus.list_all()}, roles=STAFF_ROLES,
                           role_labels=ROLE_LABELS, regions=REGIONS)


@bp.post("/admin/users")
def admin_user_create():
    _need(rbac.USER_MANAGE)
    f = request.form
    role = f.get("role")
    try:
        if role not in STAFF_ROLES:
            raise ServiceError("choose a role")
        pin = (f.get("pin") or "").strip()
        if not (pin.isdigit() and 4 <= len(pin) <= 8):
            raise ServiceError("temporary PIN must be 4 to 8 digits")
        uname = (f.get("username") or "").strip().lower()
        if len(uname) < 3 or not (f.get("display_name") or "").strip():
            raise ServiceError("username and name are required")
        bid = int(f["bureau_id"]) if (f.get("bureau_id") or "").isdigit() else None
        if role != SUPER_ADMIN and not bureaus.get(bid):
            raise ServiceError("choose a bureau")
        if role == "regional_user" and not f.get("region"):
            raise ServiceError("a regional user needs a region")
        u = users.create(uname, f["display_name"].strip(), role, hash_pin(pin),
                         created_by=_user()["username"], must_change_pin=True,
                         bureau_id=None if role == SUPER_ADMIN else bid,
                         region=f.get("region") or None)
        audit.add(_user()["username"], "user.create", target_type="user", target_id=u["id"],
                  detail={"role": role})
        flash(f"Account {u['username']} created. They must change the PIN at first sign-in.", "ok")
    except (ServiceError, ValueError) as e:
        flash(str(e), "err")
    return redirect("/admin/users")


@bp.post("/admin/users/<int:uid>/toggle")
def admin_user_toggle(uid):
    _need(rbac.USER_MANAGE)
    u = users.get_by_id(uid)
    if not u or u["role"] == "citizen":
        abort(404)
    if u["id"] == _user()["id"]:
        flash("You cannot deactivate your own account.", "err")
        return redirect("/admin/users")
    users.set_active(uid, not u["is_active"])
    audit.add(_user()["username"], "user.activate" if not u["is_active"] else "user.deactivate",
              target_type="user", target_id=uid)
    flash(f"{u['display_name']} {'activated' if not u['is_active'] else 'deactivated'}.", "ok")
    return redirect("/admin/users")


@bp.get("/admin/bureaus")
def admin_bureaus():
    _need(rbac.BUREAU_MANAGE)
    from db import complaints as _c
    load = {}
    for c in _c.all_rows():
        if c["status"] != "Disposed":
            load[c["bureau_id"]] = load.get(c["bureau_id"], 0) + 1
    staff = {}
    for u in users.list_staff():
        if u["bureau_id"]:
            staff[u["bureau_id"]] = staff.get(u["bureau_id"], 0) + 1
    return render_template("admin_bureaus.html", rows=bureaus.list_all(), load=load, staff=staff,
                           regions=REGIONS)


@bp.post("/admin/bureaus")
def admin_bureau_create():
    _need(rbac.BUREAU_MANAGE)
    f = request.form
    code, name = (f.get("code") or "").strip().upper(), (f.get("name") or "").strip()
    if not code or not name:
        flash("Code and name are required.", "err")
    else:
        try:
            b = bureaus.create(code, name, f.get("region") or None)
            audit.add(_user()["username"], "bureau.create", target_type="bureau", target_id=b["id"])
            flash(f"Bureau {code} created.", "ok")
        except ValueError as e:
            flash(str(e), "err")
    return redirect("/admin/bureaus")


@bp.get("/admin/audit")
def admin_audit():
    _need(rbac.AUDIT_VIEW)
    return render_template("audit.html", rows=audit.list_recent(300))

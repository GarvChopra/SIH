"""Super Admin portal: dashboard, queue, workflow, recurring, pulse, gaps, CRUD, audit."""
import base64
import csv
import io as _io
import secrets
import time as _time

from flask import Blueprint, Response, abort, g, redirect, render_template, request

from db import audit, evidence, grievances, locations, notices, recurring, timeline, users
from domain.constants import (CATEGORIES, LOCATION_TYPES, RESPONSIBLE_UNITS,
                              RESPONSIBLE_UNITS_FLAT, ROOM_TYPES, SLA_HOURS,
                              STATUS_TRANSITIONS, STATUSES)
from domain.rbac import (ANALYTICS_VIEW, AUDIT_VIEW, GRIEVANCE_ASSIGN,
                         GRIEVANCE_CHANGE_STATUS, GRIEVANCE_CLOSE,
                         GRIEVANCE_CORRECT_CATEGORY, GRIEVANCE_VERIFY,
                         GRIEVANCE_VERIFY_RESOLUTION, LOCATION_MANAGE, NOTICE_MANAGE,
                         USER_MANAGE, has_permission, require_permission)
from services import dashboard_service, grievance_service, intelligence_service
from services.auth_service import hash_pin, verify_pin

bp = Blueprint("admin", __name__, url_prefix="/admin",
               template_folder="../../templates")

_STATUS_RANK = {s: i for i, s in enumerate(STATUSES)}
_DONE = ("resolved", "admin_verified", "closed")

# how the queue rows are ordered for each value of the sort control
_ROW_SORTS = {
    "priority": lambda r: (-(r["priority_score"] or 0), -(r["created_at"] or 0)),
    "created":  lambda r: -(r["created_at"] or 0),
    "due":      lambda r: (r["due_at"] or 9e18, -(r["priority_score"] or 0)),
}


@bp.before_request
def _guard():
    if not g.get("current_user"):
        return redirect("/login")
    if g.current_user["role"] != "admin":
        abort(403)


@bp.context_processor
def _admin_shell_context():
    """Data every admin page's chrome needs: the top-bar notification list and
    the small nav badges. `alerts()` is cached ~60s so this stays cheap."""
    try:
        items = dashboard_service.alerts()
    except Exception:                     # noqa: BLE001 - chrome must never 500
        items = []
    by_href = {it["href"]: it["count"] for it in items}
    badges = {}
    if by_href.get("/admin/grievances?sort=due"):
        badges["/admin/grievances"] = {"n": by_href["/admin/grievances?sort=due"], "muted": False}
    if by_href.get("/admin/users"):
        badges["/admin/users"] = {"n": by_href["/admin/users"], "muted": False}
    if by_href.get("/admin/recurring"):
        badges["/admin/recurring"] = {"n": by_href["/admin/recurring"], "muted": True}
    return {"ax_alerts": items, "ax_nav_badges": badges}


def _actor():
    return g.current_user["username"]


def _get_or_404(code):
    gr = grievances.get_by_code(code)
    if not gr:
        abort(404)
    return gr


def _back(code, err=None):
    return redirect(f"/admin/grievances/{code}" + (f"?err={err}" if err else ""))


# ── dashboard ──────────────────────────────────────────────────────────────

@bp.get("/more")
def more_page():
    return render_template("admin/more.html")


@bp.get("/", strict_slashes=False)
def dashboard():
    rng = dashboard_service.resolve_range(request.args.get("range"))
    return render_template("admin/dashboard.html", d=dashboard_service.overview(rng))


@bp.get("/settings")
def settings_page():
    from db import pool
    return render_template(
        "admin/settings.html",
        me=g.current_user,
        sla=SLA_HOURS,
        categories=CATEGORIES,
        units=RESPONSIBLE_UNITS,
        location_types=LOCATION_TYPES,
        db_mode=pool.STATE["mode"],
        ok=request.args.get("ok"),
        err=request.args.get("err"),
    )


@bp.post("/settings/pin")
def settings_pin():
    u = users.get_by_username(_actor())
    cur = (request.form.get("current") or "").strip()
    new = (request.form.get("new") or "").strip()
    if not verify_pin(cur, u["pin_hash"]):
        return redirect("/admin/settings?err=Current+PIN+is+incorrect")
    if not (new.isdigit() and 4 <= len(new) <= 8):
        return redirect("/admin/settings?err=New+PIN+must+be+4-8+digits")
    users.set_pin(u["id"], hash_pin(new))
    audit.add(u["username"], "user.self_pin", target_type="user", target_id=u["id"])
    return redirect("/admin/settings?ok=1")


# ── queue ──────────────────────────────────────────────────────────────────

def _location_filters():
    """Building / floor / facility option lists for the queue filter bar,
    derived from the live location tree."""
    root = locations.campus_root()
    tops = locations.children(root["id"]) if root else []
    buildings, facilities, floors = [], [], set()
    for t in tops:
        if t["location_type"] == "building":
            buildings.append(t["name"])
            for f in locations.children(t["id"]):
                floors.add(f["name"])
        elif t["location_type"] in ("facility", "zone"):
            facilities.append(t["name"])
    return {"buildings": buildings, "facilities": facilities,
            "floors": sorted(floors)}


@bp.get("/grievances")
def queue_page():
    return render_template("admin/queue.html", categories=CATEGORIES, statuses=STATUSES,
                           units=RESPONSIBLE_UNITS_FLAT, location_types=LOCATION_TYPES,
                           loc=_location_filters())


_last_recompute = [0.0]


@bp.get("/grievances/data")
def queue_data():
    # Priority score has a slow age component (changes at most once/day per
    # grievance). Recompute it periodically, not on every poll — it issues one
    # UPDATE per open grievance, which is slow against a remote database.
    now = _time.time()
    if now - _last_recompute[0] > 900:          # 15 min
        try:
            grievance_service.recompute_open()
        except Exception as e:                   # noqa: BLE001 - never block the queue
            import logging
            logging.getLogger("unifix").warning("recompute_open skipped: %s", e)
        _last_recompute[0] = now
    sort_key = request.args.get("sort") or "priority"
    rows = grievances.list_query(
        status=request.args.get("status") or None,
        category=request.args.get("category") or None,
        responsible_unit=request.args.get("unit") or None,
        location_type=request.args.get("location_type") or None,
        building=request.args.get("building") or None,
        floor=request.args.get("floor") or None,
        room=request.args.get("room") or None,
        facility=request.args.get("facility") or None,
        search=request.args.get("search") or None,
        sort=sort_key,
        limit=500,
    )
    now = _time.time()
    active_groups = {gr["id"]: gr for gr in recurring.list_active()}
    grouped: dict[int, list] = {}
    singles = []
    for gg in rows:
        gid = gg.get("recurring_group_id")
        (grouped.setdefault(gid, []).append(gg) if gid in active_groups else singles.append(gg))

    def _overdue(x):
        return bool(x["due_at"] and now > x["due_at"] and x["status"] not in _DONE)

    out = []
    for gg in singles:
        out.append({
            "code": gg["code"], "group_id": None, "title": gg["title"],
            "category": gg["category"], "status": gg["status"],
            "location_label": gg["location_label"], "priority_score": gg["priority_score"],
            "report_count": 1, "reporter_name": gg["reporter_name"],
            "due_at": gg["due_at"], "created_at": gg["created_at"],
            "overdue": _overdue(gg), "is_group": False,
        })
    for gid, members in grouped.items():
        grp = active_groups[gid]
        lead = min(members, key=lambda m: _STATUS_RANK.get(m["status"], 0))
        out.append({
            "code": None, "group_id": gid, "title": grp["title"],
            "category": grp["category"], "status": lead["status"],
            "location_label": grp["location_label"],
            "priority_score": max(m["priority_score"] for m in members),
            "report_count": grp["report_count"],
            "reporter_name": f"{grp['reporter_count']} employees",
            "due_at": lead["due_at"],
            "created_at": max((m["created_at"] or 0) for m in members),
            "overdue": any(_overdue(m) for m in members),
            "is_group": True,
        })
    out.sort(key=_ROW_SORTS.get(sort_key, _ROW_SORTS["priority"]))
    return {"rows": out}


# ── detail + workflow actions ─────────────────────────────────────────────

@bp.get("/grievances/<code>")
def grievance_detail(code):
    gr = _get_or_404(code)
    group = recurring.get(gr["recurring_group_id"]) if gr["recurring_group_id"] else None
    members = recurring.members(group["id"]) if group else []
    return render_template(
        "admin/detail.html", g=gr, timeline=timeline.list_for(gr["id"]),
        evidence=evidence.list_for(gr["id"]), group=group, members=members,
        next_statuses=STATUS_TRANSITIONS.get(gr["status"], []),
        units=RESPONSIBLE_UNITS_FLAT, categories=CATEGORIES,
        error=request.args.get("err"),
    )


@bp.post("/grievances/<code>/verify")
@require_permission(GRIEVANCE_VERIFY)
def act_verify(code):
    gr = _get_or_404(code)
    try:
        grievance_service.transition(gr["id"], "verified", actor=_actor(), actor_role="admin")
    except grievance_service.WorkflowError as e:
        return _back(code, e.message)
    return _back(code)


@bp.post("/grievances/<code>/category")
@require_permission(GRIEVANCE_CORRECT_CATEGORY)
def act_category(code):
    gr = _get_or_404(code)
    try:
        grievance_service.correct_category(gr["id"],
                                           category=request.form.get("category", ""),
                                           actor=_actor())
    except grievance_service.WorkflowError as e:
        return _back(code, e.message)
    return _back(code)


@bp.post("/grievances/<code>/assign")
@require_permission(GRIEVANCE_ASSIGN)
def act_assign(code):
    gr = _get_or_404(code)
    try:
        grievance_service.assign(gr["id"], unit=request.form.get("unit", ""),
                                 assignee=request.form.get("assignee", "").strip(),
                                 actor=_actor())
    except grievance_service.WorkflowError as e:
        return _back(code, e.message)
    return _back(code)


@bp.post("/grievances/<code>/status")
@require_permission(GRIEVANCE_CHANGE_STATUS)
def act_status(code):
    gr = _get_or_404(code)
    to = request.form.get("to", "")
    role = g.current_user["role"]
    if to == "admin_verified" and not has_permission(role, GRIEVANCE_VERIFY_RESOLUTION):
        return _back(code, "Not allowed")
    if to == "closed" and not has_permission(role, GRIEVANCE_CLOSE):
        return _back(code, "Not allowed")
    try:
        grievance_service.transition(gr["id"], to, actor=_actor(), actor_role="admin",
                                     note=request.form.get("note") or None)
    except grievance_service.WorkflowError as e:
        return _back(code, e.message)
    return _back(code)


@bp.post("/grievances/<code>/evidence")
@require_permission(GRIEVANCE_CHANGE_STATUS)
def act_evidence(code):
    gr = _get_or_404(code)
    f = request.files.get("photo")
    b64 = base64.b64encode(f.read()).decode() if f and f.filename else ""
    mime = (f.mimetype if f else "image/jpeg") or "image/jpeg"
    try:
        grievance_service.add_resolution_evidence(
            gr["id"], kind=request.form.get("kind", "resolution_after"),
            image_b64=b64, mime=mime, note=request.form.get("note", "").strip(),
            actor=_actor())
    except grievance_service.WorkflowError as e:
        return _back(code, e.message)
    return _back(code)


@bp.post("/grievances/<code>/note")
@require_permission(GRIEVANCE_CHANGE_STATUS)
def act_note(code):
    gr = _get_or_404(code)
    grievance_service.add_note(gr["id"], actor=_actor(), actor_role="admin",
                               text=request.form.get("text", "").strip())
    return _back(code)


@bp.post("/grievances/<code>/reopen")
@require_permission(GRIEVANCE_CHANGE_STATUS)
def act_reopen(code):
    gr = _get_or_404(code)
    try:
        grievance_service.reopen(gr["id"], actor=_actor(),
                                 note=request.form.get("note") or None)
    except grievance_service.WorkflowError as e:
        return _back(code, e.message)
    return _back(code)


# ── recurring ──────────────────────────────────────────────────────────────

@bp.get("/recurring")
def recurring_page():
    groups = [{**gr, "members": recurring.members(gr["id"])}
              for gr in recurring.list_active()]
    return render_template("admin/recurring.html", groups=groups)


@bp.post("/recurring/<int:gid>/resolve")
@require_permission(GRIEVANCE_CHANGE_STATUS)
def recurring_resolve(gid):
    recurring.set_status(gid, "resolved")
    audit.add(_actor(), "recurring.resolve", target_type="recurring_group", target_id=gid)
    return redirect("/admin/recurring")


# ── pulse + gaps ──────────────────────────────────────────────────────────

@bp.get("/pulse")
@require_permission(ANALYTICS_VIEW)
def pulse_page():
    return render_template("admin/pulse.html", domains=intelligence_service.pulse(),
                           overall=intelligence_service.overall_health())


@bp.get("/gaps")
@require_permission(ANALYTICS_VIEW)
def gaps_page():
    return render_template("admin/gaps.html", gaps=intelligence_service.gaps())


@bp.get("/analytics")
@require_permission(ANALYTICS_VIEW)
def analytics_page():
    return render_template("admin/analytics.html", a=intelligence_service.analytics())


@bp.get("/analytics.csv")
@require_permission(ANALYTICS_VIEW)
def analytics_csv():
    cols = ["code", "category", "severity", "status", "priority_score", "location_label",
            "responsible_unit", "assignee", "created_at", "assigned_at", "due_at",
            "resolved_at", "closed_at", "recurring_group_id", "spam_flag"]
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for gr in grievances.list_query(limit=100000):
        w.writerow([gr.get(c) for c in cols])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=unifix_grievances.csv"})


# ── reports & downloads (time-windowed issue export) ──────────────────────

# label -> number of days back (None = all time). Order defines the button row.
_REPORT_WINDOWS = [
    ("7d", "last 7 days", 7),
    ("30d", "last 30 days", 30),
    ("60d", "last 60 days", 60),
    ("all", "all time", None),
]
_REPORT_COLS = [
    ("code", "Code"), ("reported_on", "Reported On"), ("category", "Category"),
    ("severity", "Severity"), ("status", "Status"), ("location_label", "Location"),
    ("responsible_unit", "Responsible Unit"), ("assignee", "Assignee"),
    ("reporter_name", "Reported By"), ("priority_score", "Priority"),
    ("recurring_group_id", "Recurring Group"),
]


def _resolve_window(raw):
    """Return (key, label, days). Unknown / missing values fall back to '7d'."""
    for key, label, days in _REPORT_WINDOWS:
        if raw == key:
            return key, label, days
    return _REPORT_WINDOWS[0]


def _issues_in_window(days):
    since = None if days is None else _time.time() - days * 86400
    rows = grievances.list_query(limit=100000, sort="created", created_since=since)
    return rows


def _report_row(gr):
    ts = gr.get("created_at")
    on = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(ts)) if ts else ""
    out = {"reported_on": on}
    for field, _ in _REPORT_COLS:
        if field != "reported_on":
            out[field] = gr.get(field)
    return out


@bp.get("/reports")
@require_permission(ANALYTICS_VIEW)
def reports_page():
    key, label, days = _resolve_window(request.args.get("window"))
    rows = [_report_row(g) for g in _issues_in_window(days)]
    return render_template("admin/reports.html", windows=_REPORT_WINDOWS,
                           window_key=key, window_label=label, count=len(rows),
                           rows=rows, columns=_REPORT_COLS)


@bp.get("/reports.csv")
@require_permission(ANALYTICS_VIEW)
def reports_csv():
    key, label, days = _resolve_window(request.args.get("window"))
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow([head for _, head in _REPORT_COLS])
    for g in _issues_in_window(days):
        r = _report_row(g)
        w.writerow([r.get(field) for field, _ in _REPORT_COLS])
    stamp = _time.strftime("%Y-%m-%d")
    fname = f"unifix_issues_{label.replace(' ', '_')}_{stamp}.csv"
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


# ── notices CRUD ──────────────────────────────────────────────────────────

@bp.route("/notices", methods=["GET", "POST"])
@require_permission(NOTICE_MANAGE)
def notices_page():
    if request.method == "POST":
        n = notices.create(request.form["title"].strip(),
                           request.form.get("body", "").strip(), _actor(),
                           is_published=bool(request.form.get("publish")))
        audit.add(_actor(), "notice.create", target_type="notice", target_id=n["id"])
        return redirect("/admin/notices")
    return render_template("admin/notices.html", notices=notices.list_all())


@bp.post("/notices/<int:nid>/publish")
@require_permission(NOTICE_MANAGE)
def notice_publish(nid):
    n = notices.get(nid)
    notices.publish(nid, not n["is_published"])
    audit.add(_actor(), "notice.publish", target_type="notice", target_id=nid)
    return redirect("/admin/notices")


# ── users CRUD ────────────────────────────────────────────────────────────

@bp.route("/users", methods=["GET", "POST"])
@require_permission(USER_MANAGE)
def users_page():
    if request.method == "POST":
        # Never trust the role straight off the form — whitelist it.
        role = (request.form.get("role") or "reporter").strip()
        if role not in users.VALID_ROLES:
            return render_template("admin/users.html", users=users.list_all(),
                                   error=f"Invalid role {role!r}"), 400
        # Reporters sign in with Google only — they get the same unusable,
        # never-revealed placeholder pin_hash login_with_google's auto-create
        # path uses, rather than requiring an "Initial PIN" nobody will ever
        # type. Admin creation is untouched: a real PIN is still required.
        if role == "reporter":
            pin_hash = hash_pin(secrets.token_hex(16))
        else:
            pin = (request.form.get("pin") or "").strip()
            if not pin:
                return render_template("admin/users.html", users=users.list_all(),
                                       error="Initial PIN is required for an admin account."), 400
            pin_hash = hash_pin(pin)
        try:
            u = users.create(request.form["username"].strip(),
                             request.form["display_name"].strip(),
                             role,
                             pin_hash,
                             department=request.form.get("department", "").strip() or None,
                             created_by=_actor())
        except ValueError as e:
            return render_template("admin/users.html", users=users.list_all(), error=str(e))
        # Global audit trail: record every account creation with its role, and
        # flag admin creation as its own action so privilege grants stand out.
        audit.add(_actor(), "admin.create" if role == "admin" else "user.create",
                  target_type="user", target_id=u["id"],
                  detail={"username": u["username"], "role": role})
        return redirect("/admin/users")
    return render_template("admin/users.html", users=users.list_all(), error=None)


@bp.post("/users/<int:uid>/toggle")
@require_permission(USER_MANAGE)
def user_toggle(uid):
    u = users.get_by_id(uid)
    if not u:
        abort(404)
    going_inactive = u["is_active"]
    if going_inactive:
        if u["username"] == _actor():
            return render_template("admin/users.html", users=users.list_all(),
                                   error="You cannot deactivate your own account."), 400
        if u["role"] == "admin" and users.count_active_admins() <= 1:
            return render_template("admin/users.html", users=users.list_all(),
                                   error="Cannot deactivate the last active admin."), 400
    users.set_active(uid, not u["is_active"])
    audit.add(_actor(), "user.deactivate" if going_inactive else "user.activate",
              target_type="user", target_id=uid,
              detail={"username": u["username"], "active": not u["is_active"]})
    return redirect("/admin/users")


@bp.post("/users/<int:uid>/delete")
@require_permission(USER_MANAGE)
def user_delete(uid):
    """Complete a user's deletion request: de-identify the account and their
    reporter name on historical grievances."""
    u = users.get_by_id(uid)
    if not u:
        abort(404)
    other_admins = [a for a in users.list_all(role="admin") if a["id"] != uid]
    if u["role"] == "admin" and not other_admins:
        return render_template("admin/users.html", users=users.list_all(),
                               error="Cannot delete the only admin account."), 400
    users.deidentify(uid)
    audit.add(_actor(), "user.deleted", target_type="user", target_id=uid,
              detail={"username": u["username"]})
    return redirect("/admin/users")


@bp.post("/users/<int:uid>/pin")
@require_permission(USER_MANAGE)
def user_pin(uid):
    u = users.get_by_id(uid)
    if not u:
        abort(404)
    if u["role"] != "admin":
        abort(400, description="Employees sign in with Google — there is no PIN to reset.")
    users.set_pin(uid, hash_pin(request.form["pin"].strip()))
    users.set_must_change_pin(uid, True)   # admin-reset PIN is temporary
    audit.add(_actor(), "user.reset_pin", target_type="user", target_id=uid,
              detail={"username": u["username"]})
    return redirect("/admin/users")


# ── locations CRUD ────────────────────────────────────────────────────────

# which node kinds may be created under a given parent kind
_KIND_UNDER = {
    "campus":   ("building", "facility"),
    "building": ("floor",),
    "floor":    ("room",),
    "facility": ("area",),
    "zone":     ("subzone",),
    "area":     (),
    "subzone":  (),
    "room":     (),
}
_KIND_LABEL = {"building": "Building / Block", "floor": "Floor", "room": "Room",
               "facility": "Facility", "area": "Sub-area", "subzone": "Sub-zone"}
_KIND_BUCKET = {"building": "academics_block", "facility": "facility"}


def _loc_tree_rows():
    """Flat, indented list of the whole tree for the admin page."""
    out = []

    def walk(node, depth):
        out.append({**node, "depth": depth})
        for c in sorted(locations.list_all(active_only=False),
                        key=lambda l: (l["sort_order"], l["name"])):
            if c["parent_id"] == node["id"]:
                walk(c, depth + 1)

    root = locations.campus_root()
    if root:
        walk(root, 0)
    return out


@bp.route("/locations", methods=["GET", "POST"])
@require_permission(LOCATION_MANAGE)
def locations_page():
    error = None
    if request.method == "POST":
        try:
            parent_id = int(request.form["parent_id"])
            parent = locations.get(parent_id)
            if not parent:
                raise ValueError("Unknown parent location")
            kind = request.form.get("kind", "")
            if kind not in _KIND_UNDER.get(parent["location_type"], ()):
                raise ValueError(f"Cannot add a {kind or '?'} under {parent['name']}")
            room_type = request.form.get("room_type") or None
            if kind == "room" and room_type and room_type not in ROOM_TYPES:
                raise ValueError("Unknown room type")
            loc = locations.create(
                kind, request.form["name"], parent_id=parent_id,
                room_type=room_type if kind == "room" else None,
                bucket=_KIND_BUCKET.get(kind))
            audit.add(_actor(), "location.create", target_type="location",
                      target_id=loc["id"], detail={"path": loc["full_path"]})
            return redirect("/admin/locations")
        except (ValueError, KeyError) as e:
            error = str(e)

    parents = [l for l in _loc_tree_rows()
               if _KIND_UNDER.get(l["location_type"], ())]
    return render_template("admin/locations.html", rows=_loc_tree_rows(),
                           parents=parents, kind_under=_KIND_UNDER,
                           kind_label=_KIND_LABEL, room_types=ROOM_TYPES, error=error)


@bp.post("/locations/<int:lid>/toggle")
@require_permission(LOCATION_MANAGE)
def location_toggle(lid):
    cur = locations.get(lid)
    if cur:
        locations.set_active(lid, not cur["is_active"])
        audit.add(_actor(), "location.toggle", target_type="location", target_id=lid,
                  detail={"active": not cur["is_active"]})
    return redirect("/admin/locations")


# ── audit ─────────────────────────────────────────────────────────────────

@bp.get("/audit")
@require_permission(AUDIT_VIEW)
def audit_page():
    return render_template("admin/audit.html", entries=audit.list_recent(300))

import io

from db import grievances as gdb
from services import grievance_service as gs


def _admin(client):
    client.post("/login", data={"username": "admin", "pin": "0000"})


def _report(client, user="prof.rao",
            desc="the ceiling fan is not working in this room"):
    c = client.application.test_client()
    c.post("/auth/google/mock-callback", data={"email": user, "name": user})
    return c.post("/report", json={
        "description": desc, "location_type": "academics_block", "block_no": "Block B",
        "floor": "2nd Floor", "room": "204", "photo_b64": "aGVsbG8=",
        "photo_mime": "image/jpeg"}).get_json()


# ── queue ──────────────────────────────────────────────────────────────────

def test_queue_lists_grievances(client):
    _report(client)
    _admin(client)
    data = client.get("/admin/grievances/data").get_json()
    assert len(data["rows"]) == 1
    assert data["rows"][0]["is_group"] is False


def test_queue_collapses_recurring_group(client):
    _report(client, "prof.rao")
    _report(client, "dr.iyer")
    _admin(client)
    rows = client.get("/admin/grievances/data").get_json()["rows"]
    assert len(rows) == 1
    assert rows[0]["is_group"] is True
    assert rows[0]["report_count"] == 2


def test_queue_status_filter(client):
    out = _report(client)
    _admin(client)
    g = gdb.get_by_code(out["code"])
    gs.transition(g["id"], "verified", actor="admin", actor_role="admin")
    assert len(client.get("/admin/grievances/data?status=verified").get_json()["rows"]) == 1
    assert client.get("/admin/grievances/data?status=closed").get_json()["rows"] == []


# ── workflow via routes ────────────────────────────────────────────────────

def test_full_admin_workflow_via_routes(client):
    out = _report(client)
    _admin(client)
    code = out["code"]
    assert client.post(f"/admin/grievances/{code}/verify").status_code == 302
    assert client.post(f"/admin/grievances/{code}/assign",
                       data={"unit": "Infrastructure", "assignee": "Ravi"}).status_code == 302
    client.post(f"/admin/grievances/{code}/status", data={"to": "in_progress"})

    client.post(f"/admin/grievances/{code}/status", data={"to": "resolved"})
    assert gdb.get_by_code(code)["status"] == "in_progress"    # blocked, no evidence

    client.post(f"/admin/grievances/{code}/evidence", data={
        "kind": "resolution_after", "note": "Fixed the fan",
        "photo": (io.BytesIO(b"img"), "after.jpg")},
        content_type="multipart/form-data")
    client.post(f"/admin/grievances/{code}/status", data={"to": "resolved"})
    assert gdb.get_by_code(code)["status"] == "resolved"
    client.post(f"/admin/grievances/{code}/status", data={"to": "admin_verified"})
    client.post(f"/admin/grievances/{code}/status", data={"to": "closed"})
    assert gdb.get_by_code(code)["status"] == "closed"


def test_detail_shows_error_on_bad_transition(client):
    out = _report(client)
    _admin(client)
    r = client.get(f"/admin/grievances/{out['code']}?err=Something+went+wrong")
    assert b"Something went wrong" in r.data


def test_faculty_cannot_post_admin_action(client):
    out = _report(client)
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    assert client.post(f"/admin/grievances/{out['code']}/verify").status_code == 403


# ── CRUD ───────────────────────────────────────────────────────────────────

def test_notice_crud(client):
    _admin(client)
    client.post("/admin/notices", data={"title": "Lift maintenance",
                                        "body": "Block A lift down", "publish": "1"})
    from db import notices
    assert any(n["title"] == "Lift maintenance" for n in notices.list_published())


def test_user_create_and_toggle(client):
    _admin(client)
    client.post("/admin/users", data={"username": "prof.new", "display_name": "Prof New",
                                      "department": "IT", "pin": "4321", "role": "reporter"})
    from db import users
    u = users.get_by_username("prof.new")
    assert u and u["is_active"]
    client.post(f"/admin/users/{u['id']}/toggle")
    assert users.get_by_id(u["id"])["is_active"] is False
    bad = client.application.test_client().post("/login",
            data={"username": "prof.new", "pin": "4321"})
    assert bad.status_code == 401


def test_location_add(client):
    _admin(client)
    from db import locations
    ab1 = next(c for c in locations.children(locations.campus_root()["id"])
               if c["name"] == "AB1")
    floor = locations.children(ab1["id"])[0]
    client.post("/admin/locations", data={"parent_id": floor["id"], "kind": "room",
                                          "name": "204", "room_type": "Classroom"})
    kids = [k["name"] for k in locations.children(floor["id"])]
    assert "204" in kids


def test_location_add_rejects_illegal_nesting(client):
    _admin(client)
    from db import locations
    root = locations.campus_root()["id"]
    r = client.post("/admin/locations", data={"parent_id": root, "kind": "room",
                                              "name": "999"})
    assert b"Cannot add a room" in r.data


def test_audit_page_lists_actions(client):
    out = _report(client)
    _admin(client)
    client.post(f"/admin/grievances/{out['code']}/verify")
    r = client.get("/admin/audit")
    assert b"grievance.status" in r.data


# ── pulse + gaps ───────────────────────────────────────────────────────────

def test_pulse_and_gaps_pages(client):
    _admin(client)
    assert client.get("/admin/pulse").status_code == 200
    assert client.get("/admin/gaps").status_code == 200


def test_dashboard_shows_core_sections(client):
    _admin(client)
    r = client.get("/admin")
    assert b"Campus Overview" in r.data
    assert b"Quick actions" in r.data
    assert b"Overdue issues" in r.data
    assert b"Active recurring issues" in r.data


def test_faculty_blocked_from_pulse(client):
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    assert client.get("/admin/pulse").status_code == 403


def test_admin_more_page(client):
    _admin(client)
    r = client.get("/admin/more")
    assert r.status_code == 200
    assert b"Analytics" in r.data and b"Audit Log" in r.data
    # mobile navigation is now the slide-in drawer, not a bottom bar
    assert b"ax-burger" in r.data and b'id="axSide"' in r.data

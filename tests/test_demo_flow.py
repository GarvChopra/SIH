"""The judge demo, end to end over HTTP, against the seeded offline app.

If this passes, the live demonstration script in DEMO_GUIDE.md works.
"""
import re

import pytest

from tests.conftest import login


@pytest.fixture()
def seeded_app(monkeypatch):
    monkeypatch.setenv("SEED_DEMO_DATA", "auto")
    from app import create_app
    from db import pool
    pool.reset_memory_store()
    return create_app()


def client_for(app, user, pin="1234"):
    c = app.test_client()
    login(c, user, pin)
    return c


def test_full_judge_demo(seeded_app):
    app = seeded_app
    from db import case_actions, complaints, notifications, users

    # 1. main dashboard
    sa = client_for(app, "superadmin")
    page = sa.get("/").data.decode()
    assert "Complaint monitoring" in page and "overlap alert" in page
    before = sa.get("/dashboard/data").get_json()

    # 2-4. citizen logs in, files against the empty demo institution, gets a tracking id
    cz = client_for(app, "citizen.demo", "citizen123")
    r = cz.post("/my/new", data=dict(
        aicte_id="200001", category="Unauthorized fees", title="Unauthorized examination fees",
        description="Students are being charged unauthorized examination fees at this institute."))
    assert r.status_code == 302
    tracking = re.search(r"AICTE-CMP-\d+", r.headers["Location"]).group(0)
    assert "Received" in cz.get(f"/my/c/{tracking}").data.decode()

    # 5-6. PGRC sees it, assigns and starts processing, tells the citizen
    pa = client_for(app, "pgrc.admin")
    assert tracking in pa.get("/cases?status=Pending").data.decode()
    officer = users.get_by_username("pgrc.officer")
    assert pa.post(f"/cases/{tracking}/assign", data={"officer_id": officer["id"]}).status_code == 302
    po = client_for(app, "pgrc.officer")
    po.post(f"/cases/{tracking}/status", data={"to": "Under Process", "remarks": "INTERNAL: reviewing"})
    po.post(f"/cases/{tracking}/citizen-update", data={"message": "We have written to the institute."})
    po.post(f"/cases/{tracking}/remark", data={"remarks": "INTERNAL: principal is evasive"})

    # 7-8. RB files a differently worded complaint at the same institution -> overlap detected
    rb = client_for(app, "rb.officer")
    r = rb.post("/cases/new", data=dict(
        aicte_id="200001", category="Unauthorized fees", title="Excess exam charges",
        description="The institution is collecting excess examination charges.", priority="high"))
    rb_case = re.search(r"AICTE-CMP-\d+", r.headers["Location"]).group(0)

    # 9. overlap review: RB admin sees both, the other bureau's text stays hidden
    ra = client_for(app, "rb.admin")
    ov_page = ra.get("/overlaps").data.decode()
    assert tracking in ov_page and rb_case in ov_page
    from db import case_links
    link = case_links.get_pair(complaints.get_by_number(tracking)["id"],
                              complaints.get_by_number(rb_case)["id"])
    assert link["relationship_type"] == "overlapping_proceeding"
    detail = ra.get(f"/overlaps/{link['id']}").data.decode()
    assert "Matching evidence" in detail and "Excess exam charges" in detail
    assert "Students are being charged unauthorized examination fees at this institute." not in detail

    # 10. confirm with a recorded reason -> in both histories
    r = ra.post(f"/overlaps/{link['id']}/decide",
                data={"decision": "confirmed", "reason": "Same fee demand; PGRC and RB will coordinate."},
                follow_redirects=True)
    assert b"Decision recorded" in r.data
    for num in (tracking, rb_case):
        kinds = [a["action_type"] for a in case_actions.for_complaint(complaints.get_by_number(num)["id"])]
        assert "overlap_confirmed" in kinds
    # confirmed overlap now lets RB read the PGRC case (read-only)
    page = rb.get(f"/cases/{tracking}").data.decode()
    assert "Students are being charged unauthorized examination fees at this institute." in page

    # 11. assign / transfer / escalate / resolve
    rb_off = users.get_by_username("rb.officer")
    ra.post(f"/cases/{rb_case}/assign", data={"officer_id": rb_off["id"]})
    rb.post(f"/cases/{rb_case}/status", data={"to": "Under Process"})
    ra.post(f"/cases/{rb_case}/escalate", data={"remarks": "Needs senior review given the overlap"})
    assert complaints.get_by_number(rb_case)["status"] == "Escalated"
    pa.post(f"/cases/{tracking}/status", data={
        "to": "Disposed", "remarks": "INTERNAL: refund ordered",
        "citizen_message": "The institute has been directed to refund the extra fee."})
    assert complaints.get_by_number(tracking)["status"] == "Disposed"

    # 12. histories, notifications, dashboard statistics changed
    assert "Case history" in pa.get(f"/cases/{tracking}").data.decode()
    assert notifications.unread_count(users.get_by_username("pgrc.admin")["id"]) > 0
    after = sa.get("/dashboard/data").get_json()
    assert after["total"] == before["total"] + 2
    assert after["by_status"]["Disposed"] == before["by_status"]["Disposed"] + 1
    assert after["by_status"]["Escalated"] == before["by_status"]["Escalated"] + 1

    # 13. citizen sees permitted updates only
    page = cz.get(f"/my/c/{tracking}").data.decode()
    assert "directed to refund the extra fee" in page and "We have written to the institute." in page
    assert "Closed" in page
    for secret in ("INTERNAL", "principal is evasive", "Kavita", "Excess exam charges"):
        assert secret not in page, secret
    assert cz.get(f"/my/c/{rb_case}").status_code == 404           # not theirs
    assert "AICTE-CMP" in cz.get("/my/notifications").data.decode()

    # 14. institution-wise and bureau-wise monitoring and reports
    inst = sa.get("/institutions/200001").data.decode()
    assert tracking in inst and rb_case in inst
    for rep in ("bureau-wise", "institution-wise", "overlap"):
        assert sa.get(f"/reports/{rep}").status_code == 200
        assert sa.get(f"/reports/{rep}.csv").status_code == 200
    csv = sa.get("/reports/institution-wise.csv").data.decode()
    assert "Demo Institute of Technology" in csv


# ---- mobile / API ----------------------------------------------------------

def test_mobile_api_flow_with_bearer_tokens(seeded_app):
    c = seeded_app.test_client()
    r = c.post("/api/v1/auth/login", json={"username": "citizen.demo", "password": "citizen123"})
    assert r.status_code == 200
    tok = r.get_json()
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    assert c.get("/api/v1/me", headers=h).get_json()["role"] == "citizen"
    assert c.get("/api/v1/me").status_code == 401                       # no token, no cookie
    found = c.get("/api/v1/institutions/search?q=demo", headers=h).get_json()["results"]
    assert found[0]["aicte_id"] == "200001"
    r = c.post("/api/v1/complaints", headers=h, json={
        "aicte_id": "200001", "category": "Harassment", "title": "Ragging at the hostel",
        "description": "First year students are repeatedly ragged by seniors in the hostel."})
    assert r.status_code == 201
    tid = r.get_json()["tracking_id"]
    d = c.get(f"/api/v1/complaints/{tid}", headers=h).get_json()
    assert d["status"]["label"] == "Received" and "officer" not in repr(d).lower()
    assert any(x["case_number"] == tid for x in c.get("/api/v1/complaints", headers=h).get_json()["complaints"])
    from db import complaints, users
    other = next(x for x in complaints.all_rows()
                 if x.get("citizen_id") and x["citizen_id"] != users.get_by_username("citizen.demo")["id"])
    assert c.get(f"/api/v1/complaints/{other['case_number']}", headers=h).status_code == 404
    assert c.get("/api/v1/notifications", headers=h).status_code == 200
    new = c.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert new.status_code == 200 and new.get_json()["access_token"]
    assert c.post("/api/v1/auth/refresh", json={"refresh_token": tok["access_token"]}).status_code == 401


def test_api_is_citizen_only_and_validates(seeded_app):
    c = seeded_app.test_client()
    assert c.post("/api/v1/auth/login", json={"username": "pgrc.admin", "password": "1234"}).status_code == 403
    assert c.post("/api/v1/auth/login", json={"username": "citizen.demo", "password": "bad"}).status_code == 401
    r = c.post("/api/v1/auth/register", json={"username": "api.user", "name": "Api User",
                                              "password": "longenough1", "email": "a@b.co"})
    assert r.status_code == 201
    h = {"Authorization": f"Bearer {r.get_json()['access_token']}"}
    r = c.post("/api/v1/complaints", headers=h, json={"aicte_id": "nope", "category": "Harassment",
                                                       "title": "Valid title here", "description": "x" * 40})
    assert r.status_code == 400 and "institution" in r.get_json()["error"]
    # an official's token cannot read citizen data through the API
    from services import auth_service
    from db import users
    tok = auth_service.create_access_token(users.get_by_username("pgrc.admin"))
    assert c.get("/api/v1/complaints", headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    # a citizen's token cannot open official pages
    assert c.get("/cases", headers=h).status_code == 302

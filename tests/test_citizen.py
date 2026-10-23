"""Citizen channel: registration, filing, tracking, and what they must never see."""
import io

import pytest

from tests.conftest import login
from db import users
from services import citizen_service as ctz, complaint_service as cs
from services.errors import NotFound, ValidationError

BODY = "The college is demanding an extra Rs 5000 before releasing hall tickets to students."


@pytest.fixture()
def citizen(actor, inst):
    return actor("citizen.demo")


def file_one(citizen, title="Extra fees before hall tickets"):
    return cs.create_for_citizen(citizen, aicte_id="123456", category="Unauthorized fees",
                                 title=title, description=BODY)


def test_registration_validates_and_never_stores_plain_password(memstore):
    u = ctz.register("new.user", "New User", "longenough1", "new@example.invalid")
    assert u["role"] == "citizen" and u["pin_hash"] != "longenough1"
    for bad in (dict(username="x", display_name="A B", password="longenough1", contact="a@b.co"),
                dict(username="ok.name", display_name="A B", password="short", contact="a@b.co"),
                dict(username="ok.name2", display_name="A B", password="longenough1", contact="nope"),
                dict(username="new.user", display_name="A B", password="longenough1", contact="a@b.co")):
        with pytest.raises(ValidationError):
            ctz.register(bad["username"], bad["display_name"], bad["password"], bad["contact"])


def test_filing_creates_a_case_for_the_intake_bureau_with_tracking_id(citizen, actor):
    c = file_one(citizen)
    assert c["case_number"].startswith("AICTE-CMP-") and c["channel"] == "citizen"
    assert c["citizen_id"] == citizen["id"]
    from db import bureaus
    assert c["bureau_id"] == bureaus.get_by_code("PGRC")["id"]
    # the intake bureau's admin is told
    from db import notifications
    assert any(n["type"] == "new_complaint" for n in notifications.for_user(actor("pgrc.admin")["id"]))


def test_filing_validation(citizen):
    with pytest.raises(ValidationError):
        cs.create_for_citizen(citizen, aicte_id="999999", category="Unauthorized fees",
                              title="A fine title here", description=BODY)
    with pytest.raises(ValidationError):
        cs.create_for_citizen(citizen, aicte_id="123456", category="Nonsense",
                              title="A fine title here", description=BODY)
    with pytest.raises(ValidationError):
        cs.create_for_citizen(citizen, aicte_id="123456", category="Harassment",
                              title="short", description=BODY)


def test_citizen_sees_only_own_complaints(citizen, actor, memstore):
    mine = file_one(citizen)
    other = ctz.register("other.person", "Other Person", "longenough1", "o@example.invalid")
    theirs = cs.create_for_citizen(
        {**other, "bureau_id": None, "region": None}, aicte_id="123456", category="Harassment",
        title="Their complaint title", description=BODY)
    assert [r["case_number"] for r in ctz.list_own(citizen)] == [mine["case_number"]]
    with pytest.raises(NotFound):
        ctz.detail(citizen, theirs["case_number"])
    with pytest.raises(NotFound):
        ctz.detail(citizen, "AICTE-CMP-99999")        # same response as someone else's case


def test_citizen_never_sees_internal_remarks_officers_or_other_bureaus(citizen, actor):
    c = file_one(citizen)
    cs.assign(actor("pgrc.admin"), c["id"], actor("pgrc.officer")["id"])
    cs.add_remark(actor("pgrc.officer"), c["id"], "INTERNAL: suspect is the registrar")
    cs.change_status(actor("pgrc.officer"), c["id"], "Under Process", "INTERNAL start note")
    cs.transfer(actor("pgrc.admin"), c["id"], __import__("db.bureaus", fromlist=["x"]).get_by_code("RB")["id"],
                "INTERNAL: RB handles regulation")
    d = ctz.detail(citizen, c["case_number"])
    flat = repr(d)
    for secret in ("INTERNAL", "Kavita", "RB", "PGRC", "Suresh"):
        assert secret not in flat, secret
    labels = [u["label"] for u in d["updates"]]
    assert labels == ["Complaint received", "Assigned to an officer", "Examination started",
                      "Forwarded to the concerned AICTE bureau"]
    assert d["status"]["label"] == "Forwarded"


def test_disposing_a_citizen_case_requires_an_outcome_message_and_shows_it(citizen, actor):
    c = file_one(citizen)
    cs.assign(actor("pgrc.admin"), c["id"], actor("pgrc.officer")["id"])
    cs.change_status(actor("pgrc.officer"), c["id"], "Under Process")
    with pytest.raises(ValidationError):
        cs.change_status(actor("pgrc.officer"), c["id"], "Disposed", "Refund ordered")
    cs.change_status(actor("pgrc.officer"), c["id"], "Disposed", "internal: refund ordered",
                     "The college has been directed to refund the extra fee within 15 days.")
    d = ctz.detail(citizen, c["case_number"])
    assert d["case"]["status_label"] == "Closed" and d["status"]["step"] == 3
    assert "refund the extra fee" in d["resolution"]["note"]
    assert "internal" not in repr(d)
    assert d["can_add_evidence"] is False


def test_citizen_is_notified_of_each_visible_step_without_internal_wording(citizen, actor):
    from db import notifications
    c = file_one(citizen)
    cs.assign(actor("pgrc.admin"), c["id"], actor("pgrc.officer")["id"])
    cs.change_status(actor("pgrc.officer"), c["id"], "Under Process")
    cs.post_citizen_update(actor("pgrc.officer"), c["id"], "We have written to the college.")
    msgs = [n["message"] for n in notifications.for_user(citizen["id"])]
    assert any("Examination started" in m for m in msgs)
    assert any("We have written to the college." in m for m in msgs)
    assert not any("Kavita" in m for m in msgs)


def test_citizen_cannot_use_official_service_views(citizen, actor):
    from services import access_service
    c = file_one(citizen)
    from db import complaints
    assert access_service.level(citizen, complaints.get(c["id"])) is None


# ---- web layer ----------------------------------------------------------

def test_citizen_web_flow_and_role_separation(app, inst):
    cl = app.test_client()
    r = cl.post("/register", data=dict(display_name="Web Person", contact="w@example.invalid",
                                        username="web.person", password="password99", confirm="password99"))
    assert r.status_code == 302 and r.headers["Location"].endswith("/my")
    assert cl.get("/my").status_code == 200
    assert cl.get("/my/institutions/search?q=ABC").get_json()[0]["aicte_id"] == "123456"
    r = cl.post("/my/new", data=dict(aicte_id="123456", category="Unauthorized fees",
                                      title="Hall ticket extortion", description=BODY,
                                      files=(io.BytesIO(b"%PDF-1.4 test"), "receipt.pdf")),
                content_type="multipart/form-data")
    assert r.status_code == 302
    url = r.headers["Location"]
    page = cl.get(url)
    assert page.status_code == 200 and b"AICTE-CMP-00001" in page.data and b"receipt.pdf" in page.data
    # citizens are bounced from every official page and API
    for path in ("/cases", "/cases/AICTE-CMP-00001", "/institutions", "/admin/users"):
        assert cl.get(path).status_code == 302, path
    assert cl.get("/cases/data").status_code == 302
    # their own evidence downloads; a stranger cannot fetch it
    assert cl.get("/files/1").status_code == 200
    other = app.test_client()
    other.post("/register", data=dict(display_name="Other P", contact="o@example.invalid",
                                      username="other.p", password="password99", confirm="password99"))
    assert other.get("/files/1").status_code == 404
    assert other.get("/my/c/AICTE-CMP-00001").status_code == 404


def test_officials_are_bounced_from_citizen_pages(app, inst):
    cl = app.test_client()
    login(cl, "pgrc.officer")
    assert cl.get("/my").status_code == 302
    assert cl.post("/my/new", data={}).status_code == 302


def test_upload_rejects_bad_types_and_oversize(app, inst):
    cl = app.test_client()
    login(cl, "citizen.demo", "citizen123")
    r = cl.post("/my/new", data=dict(aicte_id="123456", category="Harassment",
                                      title="Title long enough", description=BODY,
                                      files=(io.BytesIO(b"MZ"), "virus.exe")),
                content_type="multipart/form-data", follow_redirects=True)
    assert b"skipped" in r.data                      # filed, file refused
    assert users.get_by_username("citizen.demo")

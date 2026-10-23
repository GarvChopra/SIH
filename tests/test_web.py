"""Page-level smoke tests for every official role."""
import pytest

from tests.conftest import login
from services import complaint_service as cs

PAGES = ["/cases", "/cases/new", "/institutions", "/notifications", "/cases/data"]


@pytest.fixture()
def seeded(actor, inst):
    c = cs.create(actor("pgrc.officer"), aicte_id="123456", title="Hall ticket fee demand",
                  category="Unauthorized fees", description="Students are charged extra fees for hall tickets.")
    return c


@pytest.mark.parametrize("who", ["superadmin", "pgrc.admin", "pgrc.officer", "rb.officer",
                                 "ron.user", "ab.admin"])
def test_role_can_open_core_pages(login_as, seeded, who):
    cl = login_as(who)
    for p in PAGES + ["/institutions/123456", f"/cases/{seeded['case_number']}", "/"]:
        r = cl.get(p, follow_redirects=True)
        assert r.status_code == 200, (who, p, r.status_code)


def test_anonymous_is_sent_to_login(client, seeded):
    for p in ("/", "/cases", "/institutions", f"/cases/{seeded['case_number']}"):
        assert client.get(p).headers["Location"].endswith("/login")
    assert client.get("/cases/data").status_code == 401


def test_admin_pages_are_super_admin_only(login_as):
    for who in ("pgrc.admin", "rb.officer", "ron.user"):
        cl = login_as(who)
        for p in ("/admin/users", "/admin/bureaus", "/admin/audit"):
            assert cl.get(p).status_code == 403, (who, p)
    cl = login_as("superadmin")
    for p in ("/admin/users", "/admin/bureaus", "/admin/audit"):
        assert cl.get(p).status_code == 200


def test_wrong_pin_and_deactivated_user(client, memstore):
    assert client.post("/login", data={"username": "pgrc.admin", "pin": "9999"}).status_code == 401
    from db import users
    users.set_active(users.get_by_username("pgrc.officer")["id"], False)
    assert client.post("/login", data={"username": "pgrc.officer", "pin": "1234"}).status_code == 401


def test_restricted_case_page_hides_content_for_other_bureau(login_as, actor, inst):
    c = cs.create(actor("pgrc.officer"), aicte_id="123456", title="Visible only to PGRC",
                  category="Harassment", description="Unique-body-text-qwerty")
    r = login_as("rb.officer").get(f"/cases/{c['case_number']}")
    assert r.status_code == 200 and b"Unique-body-text-qwerty" not in r.data
    assert b"Visible only to PGRC" not in r.data and b"Held by PGRC" in r.data
    # and cannot act on it
    r = login_as("rb.officer").post(f"/cases/{c['case_number']}/remark", data={"remarks": "x"},
                                    follow_redirects=True)
    assert b"cannot modify" in r.data


def test_user_admin_creates_account_forced_pin_change(login_as, client):
    sa = login_as("superadmin")
    r = sa.post("/admin/users", data=dict(display_name="New Officer", username="new.officer",
                                          role="officer", bureau_id="1", pin="4321"),
                follow_redirects=True)
    assert b"new.officer" in r.data
    cl = client
    r = cl.post("/login", data={"username": "new.officer", "pin": "4321"})
    assert r.headers["Location"].endswith("/set-pin")
    assert cl.get("/cases").headers["Location"].endswith("/set-pin")

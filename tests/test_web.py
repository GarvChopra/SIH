"""Page-level smoke tests for every official role."""
import pytest

from tests.conftest import login
from services import complaint_service as cs

PAGES = ["/cases", "/cases/new", "/institutions", "/notifications", "/cases/data", "/reports",
         "/overlaps", "/overlaps?tab=decided", "/dashboard/data"]


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


def test_board_view_groups_by_status_and_masks_other_bureaus(login_as, actor, inst):
    a = cs.create(actor("pgrc.officer"), aicte_id="123456", title="Visible only to PGRC folk",
                  category="Harassment", description="Board-secret-text-zzz here for PGRC only.")
    b = cs.create(actor("rb.officer"), aicte_id="123456", title="RB own case title",
                  category="Approval violations", description="Approval has lapsed at this college.")
    rb = login_as("rb.officer")
    page = rb.get("/cases?view=board").data.decode()
    assert 'class="kanban"' in page
    for status in ("Pending", "Under Process", "Transferred", "Escalated", "Disposed"):
        assert f"k-{status.replace(' ', '-')}" in page
    assert b["case_number"] in page and "RB own case title" in page
    assert a["case_number"] in page and "Visible only to PGRC folk" not in page    # masked on the board
    assert "Board-secret-text-zzz" not in page
    only = rb.get("/cases?view=board&bureau=RB").data.decode()          # filters still apply
    assert b["case_number"] in only and a["case_number"] not in only
    assert "<table" in rb.get("/cases").data.decode()                    # list view unchanged


def _card(page, case_number):
    import re
    m = re.search(r'<a class="kcard[^"]*" href="/cases/%s" draggable="(\w+)"\s+data-case="[^"]*" '
                  r'data-from="([^"]*)" data-moves="([^"]*)"' % case_number, page)
    assert m, case_number
    return m.group(1), m.group(2), [x for x in m.group(3).split(",") if x]


def test_board_offers_only_moves_the_user_may_make(login_as, actor, inst):
    from db import complaints
    c = cs.create(actor("pgrc.officer"), aicte_id="123456", title="PGRC board card title",
                  category="Harassment", description="Some description for the board card test.")
    n = c["case_number"]
    admin = _card(login_as("pgrc.admin").get("/cases?view=board").data.decode(), n)
    assert admin[0] == "true" and admin[1] == "Pending"
    assert set(admin[2]) == {"Under Process", "Transferred", "Escalated"}
    # an officer who is not assigned, and another bureau's admin, get no draggable card
    for who in ("pgrc.officer2", "rb.admin"):
        d, _frm, moves = _card(login_as(who).get("/cases?view=board").data.decode(), n)
        assert d == "false" and moves == [], who
    # assigned officer can start work, but cannot drag to Escalated/Transferred (admins only)
    cs.assign(actor("pgrc.admin"), c["id"], actor("pgrc.officer")["id"])
    officer = _card(login_as("pgrc.officer").get("/cases?view=board").data.decode(), n)
    assert officer[2] == ["Under Process"]
    # the server refuses what the UI would not offer, even if someone posts it by hand
    r = login_as("pgrc.officer").post(f"/cases/{n}/escalate", data={"remarks": "forcing it"},
                                      follow_redirects=True)
    assert b"your role cannot do this" in r.data and complaints.get_by_number(n)["status"] == "Pending"


def test_board_move_is_validated_audited_and_returns_to_the_board(login_as, actor, inst):
    from db import case_actions, complaints
    c = cs.create(actor("pgrc.officer"), aicte_id="123456", title="Move me on the board please",
                  category="Harassment", description="Some description for the board move test.")
    n = c["case_number"]
    pa = login_as("pgrc.admin")
    r = pa.post(f"/cases/{n}/escalate", data={"remarks": "Needs senior review", "next": "/cases?view=board&bureau=2"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/cases?view=board&bureau=2")
    assert complaints.get_by_number(n)["status"] == "Escalated"
    assert case_actions.for_complaint(c["id"])[-1]["action_type"] == "escalated"
    # an unsafe 'next' is ignored: no open redirect
    for evil in ("//evil.example/x", "https://evil.example", "/admin/users", "javascript:alert(1)"):
        r = pa.post(f"/cases/{n}/remark", data={"remarks": "note", "next": evil})
        assert r.headers["Location"].endswith(f"/cases/{n}"), evil
    # an illegal move is refused by the rules, not by the UI
    r = pa.post(f"/cases/{n}/status", data={"to": "Under Process"}, follow_redirects=True)
    assert b"assign an officer" in r.data and complaints.get_by_number(n)["status"] == "Escalated"


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

"""The synthetic demo dataset: scale, planted scenarios, and consistency."""
import pytest

from db import case_links, complaints, institutions, users
from scripts import seed_aicte_demo as seed
from services import (case_view_service as views, citizen_service as ctz, dashboard_service,
                      overlap_service as ov)


@pytest.fixture()
def seeded(memstore):
    result = seed.build()
    return result


def number_of(title):
    return next(c for c in complaints.all_rows() if c["title"] == title)


def test_scale_and_idempotence(seeded):
    assert seeded["institutions"] == 30
    assert 140 <= seeded["complaints"] <= 200
    n = len(complaints.all_rows())
    assert seed.build() == {"skipped": "institutions already exist"}
    assert len(complaints.all_rows()) == n


def test_data_is_varied_enough_to_demo(seeded):
    from collections import Counter
    st = Counter(c["status"] for c in complaints.all_rows())
    assert all(st[s] > 0 for s in ("Pending", "Under Process", "Transferred", "Escalated", "Disposed"))
    assert len({c["category"] for c in complaints.all_rows()}) == 9      # 8 problem categories + queries
    assert {c["tier"] for c in complaints.all_rows()} == {1, 2, 3}
    assert len({c["bureau_id"] for c in complaints.all_rows()}) >= 6
    from services import sla_service
    states = Counter(sla_service.state(c) for c in complaints.all_rows())
    assert states["overdue"] > 0 and states["on_track"] > 0


def test_planted_abc_pair_is_flagged_and_pending(seeded, actor):
    pgrc = number_of("Unauthorized examination fees")
    rb = number_of("Excess exam charges")
    link = case_links.get_pair(pgrc["id"], rb["id"])
    assert link["relationship_type"] == "overlapping_proceeding" and link["review_status"] == "pending"
    assert rb["institution_name_stated"] == "ABC Engg. College"
    assert institutions.get(pgrc["institution_id"])["aicte_permanent_id"] == "123456"
    assert {r["link"]["id"] for r in ov.review_queue(actor("rb.admin"))} >= {link["id"]}


def test_planted_negative_same_institution_is_not_an_alert(seeded, actor):
    fq = number_of("Faculty without required degrees")
    inf = next(c for c in complaints.all_rows() if c["title"] == "Laboratories lack equipment"
               and c["institution_id"] == fq["institution_id"])
    link = case_links.get_pair(fq["id"], inf["id"])
    assert link["relationship_type"] in ("same_institution", "related")
    assert link["relationship_type"] != "overlapping_proceeding"
    assert len(views.list_cases(actor("superadmin"), {"aicte_id": "123456"})) == 4


def test_open_alerts_are_few_and_decided_examples_exist(seeded, actor):
    open_alerts = [r for r in ov.review_queue(actor("superadmin"))
                   if r["link"]["relationship_type"] == "overlapping_proceeding"]
    assert 2 <= len(open_alerts) <= 4
    decided = {r["link"]["review_status"] for r in ov.review_queue(actor("superadmin"), include_decided=True)}
    assert {"confirmed", "dismissed"} <= decided


def test_demo_institution_is_left_empty_for_the_live_demo(seeded):
    demo = institutions.get_by_aicte_id("200001")
    assert demo["name"] == "Demo Institute of Technology"
    assert complaints.for_institution(demo["id"]) == []


def test_name_only_case_waits_for_alias_approval(seeded, actor):
    from services import institution_service as isv
    rows = isv.pending_suggestions(actor("rb.admin"))
    assert rows and rows[0]["institution"]["aicte_permanent_id"] == "654321"
    assert rows[0]["complaint"]["institution_id"] is None


def test_citizen_demo_account_has_a_realistic_history(seeded, actor):
    mine = ctz.list_own(actor("citizen.demo"))
    assert len(mine) == 4
    assert {m["kind"] for m in mine} == {"Complaint", "Question"}
    assert {m["status_label"] for m in mine} == {"Received", "Under examination", "Closed"}
    closed = next(m for m in mine if m["status"] == "Disposed")
    d = ctz.detail(actor("citizen.demo"), closed["case_number"])
    assert d["resolution"] and "INTERNAL" not in repr(d)
    others = {c["citizen_id"] for c in complaints.all_rows() if c.get("citizen_id")}
    assert len(others) >= 4


def test_history_timestamps_are_backdated_and_ordered(seeded):
    from db import case_actions
    import time
    now = time.time()
    for c in complaints.all_rows()[:60]:
        ats = [a["at"] for a in case_actions.for_complaint(c["id"])]
        assert ats == sorted(ats)
        assert ats[0] <= now and max(ats) <= now + 5


def test_dashboard_on_seed_is_internally_consistent(seeded, actor):
    d = dashboard_service.build(actor("superadmin"))
    assert d["total"] == len(complaints.all_rows()) == sum(d["by_status"].values())
    assert sum(r["total"] for r in d["bureau_rows"]) == d["total"]
    assert sum(r["total"] for r in d["region_rows"]) == d["total"]
    assert d["alerts"] == len([r for r in ov.review_queue(actor("superadmin"))
                               if r["link"]["relationship_type"] == "overlapping_proceeding"])
    assert d["overdue"] == len(views.list_cases(actor("superadmin"), {"deadline": "overdue"}))


def test_app_boots_offline_with_seed_data_and_every_page_loads(monkeypatch):
    monkeypatch.setenv("SEED_DEMO_DATA", "auto")
    from app import create_app
    from db import pool
    pool.reset_memory_store()
    app = create_app()
    from tests.conftest import login
    cl = app.test_client()
    login(cl, "superadmin")
    for p in ("/", "/cases", "/overlaps", "/institutions", "/institutions/123456", "/reports",
              "/reports/ageing", "/notifications", "/admin/users", "/admin/bureaus", "/admin/audit"):
        assert cl.get(p).status_code == 200, p
    assert len(complaints.all_rows()) > 140
    c = app.test_client()
    login(c, "citizen.demo", "citizen123")
    assert c.get("/my").status_code == 200


def test_every_demo_account_can_log_in(monkeypatch):
    monkeypatch.setenv("SEED_DEMO_DATA", "auto")
    from app import create_app
    from db import pool, seeds
    pool.reset_memory_store()
    app = create_app()
    from tests.conftest import login
    for a in seeds.DEMO_ACCOUNTS:
        login(app.test_client(), a["username"], a["pin"])
    login(app.test_client(), "citizen.demo", "citizen123")
    login(app.test_client(), "priya.nair", "demo12345")

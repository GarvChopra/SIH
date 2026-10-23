"""Tiers: Tier 1 Query, Tier 2 Problem, Tier 3 Escalated; tier board and dashboard."""
import time

import pytest

from db import case_links, complaints
from services import (case_view_service as views, citizen_service as ctz, complaint_service as cs,
                      dashboard_service)
from services.errors import ValidationError

DAY = 86400
Q_TITLE, Q_DESC = "Is this college approved?", "Please confirm whether this college is approved by AICTE."
P_TITLE, P_DESC = "Unauthorized examination fees", "Students are being charged unauthorized examination fees."


def query(actor, who="pgrc.officer", aicte="123456", **kw):
    return cs.create(actor(who), aicte_id=aicte, title=Q_TITLE, description=Q_DESC, tier=1, **kw)


def problem(actor, who="pgrc.officer", aicte="123456", cat="Unauthorized fees", t=P_TITLE, d=P_DESC):
    return cs.create(actor(who), aicte_id=aicte, title=t, description=d, category=cat)


def test_query_gets_the_query_category_and_a_seven_day_deadline(actor, inst):
    q = query(actor, category="Harassment")                       # a category passed with a query is ignored
    assert q["tier"] == 1 and q["category"] == "General query"
    assert q["expected_disposal_at"] == pytest.approx(q["received_at"] + 7 * DAY)


def test_problem_defaults_to_tier_2_and_needs_a_real_category(actor, inst):
    p = problem(actor)
    assert p["tier"] == 2 and p["expected_disposal_at"] == pytest.approx(p["received_at"] + 30 * DAY)
    for bad_cat in (None, "", "Nonsense", "General query"):
        with pytest.raises(ValidationError):
            cs.create(actor("pgrc.officer"), aicte_id="123456", title=P_TITLE, description=P_DESC,
                      category=bad_cat)
    with pytest.raises(ValidationError):
        cs.create(actor("pgrc.officer"), aicte_id="123456", title=P_TITLE, description=P_DESC, tier=7)


def test_escalating_promotes_to_tier_3_and_it_sticks(actor, inst):
    p = problem(actor)
    cs.escalate(actor("pgrc.admin"), p["id"], "Needs senior review")
    assert complaints.get(p["id"])["tier"] == 3
    q = query(actor)
    cs.escalate(actor("pgrc.admin"), q["id"], "Complex legal question")
    assert complaints.get(q["id"])["tier"] == 3


def test_queries_never_take_part_in_overlap_detection(actor, inst):
    problem(actor)
    cs.create(actor("rb.officer"), aicte_id="123456", title="Is the exam fee allowed?",
              description="Students are being charged unauthorized examination fees, is that allowed?", tier=1)
    assert case_links.all_rows() == []                            # a question is not a proceeding
    two = [query(actor, "pgrc.officer"), query(actor, "rb.officer")]
    assert case_links.all_rows() == [] and len(two) == 2


def test_tier_filter_and_tier_is_visible_even_on_restricted_cases(actor, inst):
    q, p = query(actor), problem(actor)
    esc = problem(actor, cat="Harassment", t="Ragging in hostel", d="Seniors are ragging first year students.")
    cs.escalate(actor("pgrc.admin"), esc["id"], "Serious")
    su = actor("superadmin")
    assert [r["case_number"] for r in views.list_cases(su, {"tier": "1"})] == [q["case_number"]]
    assert {r["case_number"] for r in views.list_cases(su, {"tier": "2"})} == {p["case_number"]}
    assert {r["case_number"] for r in views.list_cases(su, {"tier": "3"})} == {esc["case_number"]}
    rows = {r["case_number"]: r for r in views.list_cases(actor("rb.officer"))}
    assert rows[q["case_number"]]["restricted"] and rows[q["case_number"]]["tier"] == 1


def test_board_by_tier_has_three_lanes_with_their_own_status_columns(login_as, actor, inst):
    q, p = query(actor), problem(actor)
    esc = problem(actor, cat="Harassment", t="Ragging in hostel", d="Seniors are ragging first year students.")
    cs.escalate(actor("pgrc.admin"), esc["id"], "Serious")
    page = login_as("pgrc.admin").get("/cases?view=board&group=tier").data.decode()
    assert page.count('class="kanban"') == 3
    for label in ("Tier 1 &middot; Query", "Tier 2 &middot; Problem", "Tier 3 &middot; Escalated"):
        assert label in page
    assert page.count('class="kcol k-Pending"') == 3                # every lane has all five columns
    lanes = page.split('class="lane-head')
    assert q["case_number"] in lanes[1] and p["case_number"] in lanes[2] and esc["case_number"] in lanes[3]
    assert q["case_number"] not in lanes[2] and p["case_number"] not in lanes[3]
    # the default board is still one lane grouped by status
    assert login_as("pgrc.admin").get("/cases?view=board").data.decode().count('class="kanban"') == 1


def test_list_shows_a_tier_column_and_filter(login_as, actor, inst):
    query(actor); problem(actor)
    page = login_as("pgrc.admin").get("/cases").data.decode()
    assert "<th>Tier</th>" in page and "T1 Query" in page and "T2 Problem" in page
    only = login_as("pgrc.admin").get("/cases?tier=1").data.decode()
    assert "T1 Query" in only and "T2 Problem" not in only


def test_dashboard_tier_board_counts_match_the_stored_data(login_as, actor, inst):
    query(actor); query(actor, "rb.officer")
    p = problem(actor); problem(actor, "rb.officer", cat="Harassment", t="Ragging in hostel",
                                d="Seniors are ragging first year students.")
    e = problem(actor, "ab.officer", cat="Approval violations", t="Approval has lapsed",
                d="The approval lapsed but admissions continue.")
    cs.escalate(actor("ab.admin"), e["id"], "Serious")
    d = dashboard_service.build(actor("superadmin"))
    lanes = {l["tier"]: l for l in d["tier_board"]}
    rows = complaints.all_rows()
    for t in (1, 2, 3):
        assert lanes[t]["total"] == sum(1 for c in rows if c["tier"] == t)
        for cell in lanes[t]["cells"]:
            assert cell["count"] == sum(1 for c in rows if c["tier"] == t and c["status"] == cell["status"])
    assert (lanes[1]["total"], lanes[2]["total"], lanes[3]["total"]) == (2, 2, 1)
    assert sum(l["total"] for l in d["tier_board"]) == d["total"]
    page = login_as("superadmin").get("/").data.decode()
    assert "Tier-wise workflow board" in page and "/cases?view=board&amp;group=tier&amp;tier=2&amp;status=Pending" in page


def test_dashboard_tier_cards_are_masked_for_other_bureaus(actor, inst):
    problem(actor, t="PGRC private title xyzzy", d="Students are charged unauthorized exam fees.")
    d = dashboard_service.build(actor("rb.officer"))
    flat = repr(d["tier_board"])
    assert "xyzzy" not in flat and "Restricted" in flat


def test_regional_user_dashboard_tier_board_is_limited_to_their_region(actor, inst):
    problem(actor); problem(actor, aicte="654321", cat="Harassment", t="Ragging in hostel",
                            d="Seniors are ragging first year students.")
    d = dashboard_service.build(actor("ron.user"))
    assert sum(l["total"] for l in d["tier_board"]) == 1


# ---- citizens and API ----------------------------------------------------

def test_citizen_can_ask_a_question_and_sees_it_as_a_question(actor, inst):
    cit = actor("citizen.demo")
    q = cs.create_for_citizen(cit, aicte_id="123456", tier=1, title="Is this college approved?",
                              description="Please confirm if this college is approved.")
    assert q["tier"] == 1 and q["category"] == "General query"
    mine = ctz.list_own(cit)[0]
    assert mine["kind"] == "Question"
    assert ctz.detail(cit, q["case_number"])["case"]["kind"] == "Question"
    with pytest.raises(ValidationError):                         # still needs some detail
        cs.create_for_citizen(cit, aicte_id="123456", tier=1, title="Is this college approved?",
                              description="short")
    p = cs.create_for_citizen(cit, aicte_id="123456", category="Harassment", title="Ragging in the hostel",
                              description="First year students are being ragged by seniors in the hostel.")
    assert p["tier"] == 2 and ctz.detail(cit, p["case_number"])["case"]["kind"] == "Complaint"


def test_citizen_forms_and_api_offer_the_choice(app, inst):
    c = app.test_client()
    from tests.conftest import login
    login(c, "citizen.demo", "citizen123")
    html = c.get("/my/new").data.decode()
    assert 'name="tier" value="1"' in html and "Ask a question" in html and "Report a problem" in html
    r = c.post("/my/new", data=dict(aicte_id="123456", tier="1", title="Is this college approved?",
                                    description="Please confirm if this college is approved by AICTE."))
    assert r.status_code == 302
    assert complaints.all_rows()[0]["tier"] == 1
    # API
    tok = c.post("/api/v1/auth/login", json={"username": "citizen.demo", "password": "citizen123"}).get_json()
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    r = c.post("/api/v1/complaints", headers=h, json={"aicte_id": "123456", "type": "query",
                                                       "title": "Is the hostel compulsory?",
                                                       "description": "Is hostel stay compulsory in year one?"})
    assert r.status_code == 201 and r.get_json()["complaint"]["kind"] == "Question"
    assert "types" in c.get("/api/v1/categories", headers=h).get_json()


def test_official_form_has_the_type_choice_and_registers_a_query(login_as, inst):
    pa = login_as("pgrc.officer")
    assert 'name="tier"' in pa.get("/cases/new").data.decode()
    r = pa.post("/cases/new", data=dict(aicte_id="123456", tier="1", title="Approval status query",
                                        description="Is this college approved for CSE?"))
    assert r.status_code == 302
    assert complaints.all_rows()[0]["tier"] == 1


def test_reports_include_the_query_category(actor, inst):
    from services import report_service
    q = query(actor)
    cs.assign(actor("pgrc.admin"), q["id"], actor("pgrc.officer")["id"])
    cs.change_status(actor("pgrc.officer"), q["id"], "Under Process")
    cs.change_status(actor("pgrc.officer"), q["id"], "Disposed", "Answered by email")
    rows = {r[0]: r for r in report_service.build("disposal-rate", actor("superadmin"))["rows"]}
    assert rows["General query"][1:4] == [1, 1, "100%"]

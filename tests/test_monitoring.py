"""Dashboard figures, SLA states, notifications, reports."""
import csv
import io
import time

import pytest

from db import complaints, notifications
from services import (complaint_service as cs, dashboard_service, report_service, sla_service)

DAY = 86400


def file(actor, who, title, desc, cat="Unauthorized fees", aicte="123456", **kw):
    return cs.create(actor(who), aicte_id=aicte, title=title, description=desc, category=cat, **kw)


@pytest.fixture()
def mix(actor, inst):
    """A small known register: 2 PGRC, 1 RB, 1 disposed, 1 under process, 1 overdue."""
    a = file(actor, "pgrc.officer", "Hostel water shortage", "No clean water in the hostel block.",
             "Infrastructure deficiency")
    b = file(actor, "pgrc.officer", "Marksheets withheld", "Marksheets are withheld for pending dues.",
             "Certificates withheld")
    c = file(actor, "rb.officer", "Approval lapsed", "Affiliation expired but admissions continue.",
             "Approval violations", aicte="654321")
    d = file(actor, "ab.officer", "Faculty degrees", "Faculty do not hold masters degrees.",
             "Faculty qualifications", aicte="654321")
    cs.assign(actor("pgrc.admin"), a["id"], actor("pgrc.officer")["id"])
    cs.change_status(actor("pgrc.officer"), a["id"], "Under Process")
    cs.assign(actor("ab.admin"), d["id"], actor("ab.officer")["id"])
    cs.change_status(actor("ab.officer"), d["id"], "Under Process")
    cs.change_status(actor("ab.officer"), d["id"], "Disposed", "Qualifications verified")
    # make b overdue: received 40 days ago, certificates SLA is 20 days
    complaints.update(b["id"], received_at=time.time() - 40 * DAY,
                      expected_disposal_at=sla_service.disposal_deadline("Certificates withheld",
                                                                          time.time() - 40 * DAY))
    return a, b, c, d


def test_dashboard_numbers_match_the_stored_data(actor, mix):
    d = dashboard_service.build(actor("superadmin"))
    rows = complaints.all_rows()
    assert d["total"] == len(rows) == 4
    for status in ("Pending", "Under Process", "Transferred", "Escalated", "Disposed"):
        assert d["by_status"][status] == sum(1 for c in rows if c["status"] == status)
    assert d["by_status"] == {"Pending": 2, "Under Process": 1, "Transferred": 0,
                              "Escalated": 0, "Disposed": 1}
    assert d["active"] == 3 and d["overdue"] == 1
    by_b = {r["bureau"]["code"]: r for r in d["bureau_rows"]}
    assert by_b["PGRC"]["total"] == 2 and by_b["PGRC"]["active"] == 2 and by_b["PGRC"]["overdue"] == 1
    assert by_b["AB"]["disposed"] == 1 and by_b["RB"]["active"] == 1
    assert sum(r["total"] for r in d["bureau_rows"]) == d["total"]
    regions = {r["region"]: r for r in d["region_rows"]}
    assert regions["North"]["total"] == 2 and regions["South"]["total"] == 2
    top = d["top_institutions"][0]
    assert top["institution"]["aicte_permanent_id"] in ("123456", "654321")


def test_dashboard_changes_when_data_changes(actor, mix):
    before = dashboard_service.build(actor("superadmin"))["total"]
    file(actor, "pgrc.officer", "Another matter", "A wholly different matter about toilets.", "Harassment")
    assert dashboard_service.build(actor("superadmin"))["total"] == before + 1


def test_dashboard_for_regional_user_is_limited_to_region(actor, mix):
    d = dashboard_service.build(actor("ron.user"))
    assert d["total"] == 2                       # only the North institution's cases
    assert all(r["region"] == "North" for r in d["region_rows"])


def test_dashboard_page_links_numbers_to_filtered_lists(login_as, mix):
    page = login_as("superadmin").get("/").data.decode()
    for href in ("/cases?status=Pending", "/cases?deadline=overdue", "/overlaps", "/cases?status=Disposed"):
        assert href in page, href


def test_filters_agree_with_dashboard_counts(login_as, actor, mix):
    from services import case_view_service as views
    u = actor("superadmin")
    assert len(views.list_cases(u, {"status": "Pending"})) == 2
    assert len(views.list_cases(u, {"deadline": "overdue"})) == 1
    assert len(views.list_cases(u, {"region": "South"})) == 2
    assert len(views.list_cases(u, {"bureau": "PGRC"})) == 2
    assert len(views.list_cases(u, {"aicte_id": "6543"})) == 2
    assert len(views.list_cases(u, {"institution": "southern"})) == 2
    assert len(views.list_cases(u, {"category": "Harassment"})) == 0
    assert len(views.list_cases(u, {"officer": str(actor("pgrc.officer")["id"])})) == 1
    now = time.time()
    assert len(views.list_cases(u, {"date_from": now - DAY, "date_to": now + DAY})) == 3


# ---- SLA -------------------------------------------------------------------

def test_sla_states(actor, inst):
    c = file(actor, "pgrc.officer", "Fee matter here", "Students are charged unauthorized fees.")
    rec, due = c["received_at"], c["expected_disposal_at"]
    assert sla_service.state(c, rec + 1) == "on_track"
    assert sla_service.state(c, due - (due - rec) * 0.19) == "due_soon"       # last 20% of window
    assert sla_service.state(c, due - (due - rec) * 0.21) == "on_track"
    assert sla_service.state(c, due + 1) == "overdue"
    assert sla_service.state({**c, "status": "Disposed"}, due + 10) is None


def test_sweep_is_idempotent_one_notice_per_case_per_type(actor, mix):
    first = sla_service.run_sweep()
    assert first == 1            # the overdue case has no officer yet: just the PGRC bureau admin
    assert sla_service.run_sweep() == 0                    # running again creates nothing
    overdue = [n for u in ("pgrc.admin",) for n in notifications.for_user(actor(u)["id"])
               if n["type"] == "overdue"]
    assert len(overdue) == 1
    # a case that later becomes due-soon gets its own, separate notice
    later = time.time() + 55 * DAY
    assert sla_service.run_sweep(later) > 0 and sla_service.run_sweep(later) == 0


def test_sweep_skips_disposed(actor, mix):
    a, b, c, d = mix
    sla_service.run_sweep(time.time() + 365 * DAY)
    assert not any(n["complaint_id"] == d["id"] for n in notifications.for_user(actor("ab.admin")["id"]))


# ---- reports ---------------------------------------------------------------

@pytest.mark.parametrize("name", list(report_service.REPORTS))
def test_every_report_builds_and_exports_csv(actor, mix, name):
    r = report_service.build(name, actor("superadmin"))
    assert r["columns"]
    rows = list(csv.reader(io.StringIO(report_service.to_csv(r))))
    assert rows[2] == [str(c) for c in r["columns"]]
    assert len(rows) == 3 + len(r["rows"])


def test_bureau_report_matches_dashboard(actor, mix):
    r = report_service.build("bureau-wise", actor("superadmin"))
    by = {row[0]: row for row in r["rows"]}
    assert by["PGRC"][2] == 2 and by["AB"][2] == 1
    assert by["AB"][-1] == "100%"                          # 1 of 1 disposed
    assert r["columns"][:3] == ["Bureau", "Name", "Total"]


def test_deadline_compliance_and_ageing(actor, mix):
    ageing = report_service.build("ageing", actor("superadmin"))
    assert ageing["rows"][0][6] >= 40 and ageing["rows"][0][7] == "31-60"     # oldest first
    comp = {row[0]: row for row in report_service.build("deadline-compliance", actor("superadmin"))["rows"]}
    assert comp["AB"][1:5] == [1, 1, 0, "100%"]


def test_reports_never_contain_case_text(actor, mix):
    for name in report_service.REPORTS:
        flat = report_service.to_csv(report_service.build(name, actor("superadmin")))
        assert "Marksheets are withheld" not in flat and "No clean water" not in flat


def test_csv_neutralises_formula_injection():
    r = {"title": "t", "generated_at": 0, "columns": ["a"], "rows": [["=cmd()"], ["-5"], ["+1+1"]]}
    out = report_service.to_csv(r)
    lines = out.splitlines()
    assert "'=cmd()" in lines and "'+1+1" in lines and "-5" in lines   # negative number left alone


def test_csv_download_route_and_audit(login_as):
    cl = login_as("pgrc.admin")
    r = cl.get("/reports/bureau-wise.csv")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert "attachment" in r.headers["Content-Disposition"]
    assert cl.get("/reports/nope").status_code == 404
    from db import audit
    assert any(e["action"] == "report.export" for e in audit.list_recent())


def test_overlap_report_is_limited_to_involved_bureaus(actor, inst):
    file(actor, "pgrc.officer", "Exam fee", "Students are charged unauthorized examination fees.")
    file(actor, "rb.officer", "Exam charges", "The institution is collecting excess examination charges.")
    assert len(report_service.build("overlap", actor("superadmin"))["rows"]) == 1
    assert len(report_service.build("overlap", actor("rb.admin"))["rows"]) == 1
    assert report_service.build("overlap", actor("ab.admin"))["rows"] == []

"""Reports: each returns (columns, rows). Used for the on-screen/print view and CSV.

Reports are computed from the same stored data as the dashboard. They contain
counts and case identifiers only, never titles, descriptions or remarks, so a
report cannot leak a restricted case. The overlap report is limited to overlaps
the user's bureau is part of (super admin: all) because its evidence is content.
"""
from __future__ import annotations

import csv
import io
import time

from db import bureaus, case_actions, case_links, complaints, institutions
from domain.constants import (ACTIVE_STATUSES, ALL_CATEGORIES, DISPOSED, OVERDUE, REGIONS,
                              STATUSES)
from services import access_service, overlap_service, sla_service

DAY = 86400.0

REPORTS = {
    "bureau-wise": ("Bureau-wise workload", "Cases by bureau and status, with overdue cases and disposal rate."),
    "region-wise": ("Region-wise workload", "Cases by the institution's region."),
    "institution-wise": ("Institution-wise cases", "Every institution with its active cases, bureaus involved and overlaps."),
    "ageing": ("Ageing of pending cases", "Open cases, how long each has been waiting, and how many are past deadline."),
    "disposal-rate": ("Disposal rate by category", "Cases disposed, disposal rate and average days to disposal."),
    "deadline-compliance": ("Deadline compliance", "Share of disposed cases closed within their SLA, per bureau."),
    "overlap": ("Overlap report", "Detected overlaps, their evidence summary and the official decision."),
}


def _rows(user):
    return [c for c in complaints.all_rows() if access_service.in_scope(user, c)]


def _disposed_at(cid):
    t = None
    for a in case_actions.for_complaint(cid):
        if a["action_type"] == "disposed":
            t = a["at"]
    return t


def _pct(n, d):
    return f"{(100 * n / d):.0f}%" if d else "n/a"


def _bureau_wise(user, now):
    cols = ["Bureau", "Name", "Total"] + STATUSES + ["Overdue", "Disposal rate"]
    out = []
    rows = _rows(user)
    for b in bureaus.list_all():
        mine = [c for c in rows if c["bureau_id"] == b["id"]]
        by = {s: sum(1 for c in mine if c["status"] == s) for s in STATUSES}
        over = sum(1 for c in mine if sla_service.state(c, now) == OVERDUE)
        out.append([b["code"], b["name"], len(mine)] + [by[s] for s in STATUSES]
                   + [over, _pct(by[DISPOSED], len(mine))])
    return cols, out


def _region_wise(user, now):
    cols = ["Region", "Total", "Active", "Disposed", "Overdue"]
    inst = {i["id"]: i for i in institutions.list_all()}
    out = []
    rows = _rows(user)
    for name in REGIONS + [None]:
        mine = [c for c in rows if (inst.get(c["institution_id"]) or {}).get("region") == name]
        if name is None:
            mine = [c for c in rows if not (inst.get(c["institution_id"]) or {}).get("region")]
        if not mine:
            continue
        out.append([name or "Not set / unlinked", len(mine),
                    sum(1 for c in mine if c["status"] != DISPOSED),
                    sum(1 for c in mine if c["status"] == DISPOSED),
                    sum(1 for c in mine if sla_service.state(c, now) == OVERDUE)])
    return cols, out


def _institution_wise(user, now):
    cols = ["AICTE ID", "Institution", "Region", "Total", "Active", "Disposed", "Bureaus involved",
            "Open overlaps"]
    rows = _rows(user)
    flagged = {}
    for l in case_links.all_rows():
        if l["relationship_type"] == "overlapping_proceeding" and l["review_status"] == "pending":
            c = complaints.get(l["complaint_a_id"])
            if c:
                flagged[c["institution_id"]] = flagged.get(c["institution_id"], 0) + 1
    out = []
    for i in institutions.list_all():
        mine = [c for c in rows if c["institution_id"] == i["id"]]
        if not mine:
            continue
        act = [c for c in mine if c["status"] != DISPOSED]
        involved = sorted({bureaus.get(c["bureau_id"])["code"] for c in act})
        out.append([i["aicte_permanent_id"], i["name"], i["region"] or "", len(mine), len(act),
                    len(mine) - len(act), ", ".join(involved), flagged.get(i["id"], 0)])
    out.sort(key=lambda r: (-r[4], r[1]))
    return cols, out


def _ageing(user, now):
    cols = ["Case", "Bureau", "Institution", "Category", "Status", "Received", "Age (days)",
            "Age bucket", "Due by", "Deadline"]
    inst = {i["id"]: i for i in institutions.list_all()}
    out = []
    for c in _rows(user):
        if c["status"] not in ACTIVE_STATUSES:
            continue
        age = int((now - c["received_at"]) // DAY)
        bucket = "0-7" if age <= 7 else "8-15" if age <= 15 else "16-30" if age <= 30 \
            else "31-60" if age <= 60 else "60+"
        i = inst.get(c["institution_id"])
        out.append([c["case_number"], bureaus.get(c["bureau_id"])["code"],
                    i["name"] if i else "Unlinked", c["category"], c["status"],
                    time.strftime("%Y-%m-%d", time.localtime(c["received_at"])), age, bucket,
                    time.strftime("%Y-%m-%d", time.localtime(c["expected_disposal_at"])),
                    (sla_service.state(c, now) or "").replace("_", " ")])
    out.sort(key=lambda r: -r[6])
    return cols, out


def _disposal_rate(user, now):
    cols = ["Category", "Total", "Disposed", "Disposal rate", "Avg days to dispose"]
    rows = _rows(user)
    out = []
    for cat in ALL_CATEGORIES:
        mine = [c for c in rows if c["category"] == cat]
        if not mine:
            continue
        done = [c for c in mine if c["status"] == DISPOSED]
        days = [(_disposed_at(c["id"]) - c["received_at"]) / DAY for c in done if _disposed_at(c["id"])]
        out.append([cat, len(mine), len(done), _pct(len(done), len(mine)),
                    f"{sum(days) / len(days):.1f}" if days else "n/a"])
    return cols, out


def _deadline_compliance(user, now):
    cols = ["Bureau", "Disposed", "On time", "Late", "Compliance", "Open & overdue"]
    rows = _rows(user)
    out = []
    for b in bureaus.list_all():
        mine = [c for c in rows if c["bureau_id"] == b["id"]]
        done = [c for c in mine if c["status"] == DISPOSED]
        on_time = late = 0
        for c in done:
            t = _disposed_at(c["id"])
            if t is None:
                continue
            if t <= c["expected_disposal_at"]:
                on_time += 1
            else:
                late += 1
        out.append([b["code"], len(done), on_time, late, _pct(on_time, on_time + late),
                    sum(1 for c in mine if sla_service.state(c, now) == OVERDUE)])
    return cols, out


def _overlap(user, now):
    cols = ["Institution", "AICTE ID", "Case A", "Bureau A", "Case B", "Bureau B", "Type",
            "Similarity", "Mode", "Status", "Decided by", "Reason"]
    out = []
    pending = overlap_service.review_queue(user)
    decided = overlap_service.review_queue(user, include_decided=True)
    for r in pending + decided:
        l, ev = r["link"], r["link"]["evidence"] or {}
        out.append([r["institution"]["name"], r["institution"]["aicte_permanent_id"],
                    r["a"]["case_number"], r["bureau_a"], r["b"]["case_number"], r["bureau_b"],
                    l["relationship_type"].replace("_", " "), f"{(l['score'] or 0) * 100:.0f}%",
                    ev.get("scores", {}).get("mode", ""), l["review_status"].replace("_", " "),
                    r["reviewer"]["display_name"] if r["reviewer"] else "",
                    l["reason"] or ""])
    return cols, out


_BUILDERS = {"bureau-wise": _bureau_wise, "region-wise": _region_wise,
             "institution-wise": _institution_wise, "ageing": _ageing,
             "disposal-rate": _disposal_rate, "deadline-compliance": _deadline_compliance,
             "overlap": _overlap}


def build(name: str, user: dict):
    if name not in _BUILDERS:
        raise KeyError(name)
    cols, rows = _BUILDERS[name](user, time.time())
    return {"name": name, "title": REPORTS[name][0], "about": REPORTS[name][1],
            "columns": cols, "rows": rows, "generated_at": time.time()}


def to_csv(report: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"SAMANVAY report: {report['title']}"])
    w.writerow([f"Generated {time.strftime('%Y-%m-%d %H:%M', time.localtime(report['generated_at']))}",
                "Synthetic demonstration data"])
    w.writerow(report["columns"])
    for row in report["rows"]:
        w.writerow([_safe(v) for v in row])
    return buf.getvalue()


def _safe(v):
    """Neutralise spreadsheet formula injection from user-controlled text."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") and not s.lstrip("-").replace(".", "").isdigit() else s

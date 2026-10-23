"""Live dashboard figures. Every number is computed from the stored complaints on
each request; nothing is cached or hard-coded. Each figure carries the URL of the
filtered case list it counts, so a number can always be traced to its cases.

Summary-level visibility is global (anyone can see THAT a case exists), so counts
are system-wide for staff. A regional user's figures are limited to their region.
"""
from __future__ import annotations

import time

from db import bureaus, case_links, complaints, institutions
from domain.constants import (ACTIVE_STATUSES, DISPOSED, DUE_SOON, ESCALATED, OVERDUE,
                              OVERLAPPING_PROCEEDING, PENDING, PENDING_REVIEW, REGIONS,
                              STATUSES, TRANSFERRED, UNDER_PROCESS)
from services import access_service, overlap_service, sla_service

STATUS_COLORS = {PENDING: "#c98a12", UNDER_PROCESS: "#1a3f86", TRANSFERRED: "#6b46c1",
                 ESCALATED: "#c5352b", DISPOSED: "#18875a"}


def _visible(user: dict) -> list:
    return [c for c in complaints.all_rows() if access_service.in_scope(user, c)]


def _region_of(c, inst_by_id):
    inst = inst_by_id.get(c["institution_id"])
    return (inst["region"] if inst and inst["region"] else None) if inst else None


def build(user: dict) -> dict:
    now = time.time()
    rows = _visible(user)
    inst_by_id = {i["id"]: i for i in institutions.list_all()}
    by_status = {s: 0 for s in STATUSES}
    sla = {OVERDUE: 0, DUE_SOON: 0}
    for c in rows:
        by_status[c["status"]] += 1
        st = sla_service.state(c, now)
        if st in sla:
            sla[st] += 1
    total = len(rows)
    active = total - by_status[DISPOSED]

    alerts = [r for r in overlap_service.review_queue(user)
              if r["link"]["relationship_type"] == OVERLAPPING_PROCEEDING]

    # bureau-wise workload
    bureau_rows = []
    for b in bureaus.list_all():
        mine = [c for c in rows if c["bureau_id"] == b["id"]]
        act = [c for c in mine if c["status"] != DISPOSED]
        bureau_rows.append({"bureau": b, "total": len(mine), "active": len(act),
                            "overdue": sum(1 for c in act if sla_service.state(c, now) == OVERDUE),
                            "disposed": len(mine) - len(act)})
    bureau_rows.sort(key=lambda r: -r["active"])

    # region-wise workload (by the institution's region)
    region_rows = []
    for name in REGIONS + [None]:
        mine = [c for c in rows if _region_of(c, inst_by_id) == name]
        if not mine:
            continue
        act = [c for c in mine if c["status"] != DISPOSED]
        region_rows.append({"region": name or "Unlinked / not set", "key": name or "",
                            "total": len(mine), "active": len(act),
                            "overdue": sum(1 for c in act if sla_service.state(c, now) == OVERDUE)})

    # top institutions by active cases
    per_inst = {}
    for c in rows:
        if c["institution_id"] and c["status"] != DISPOSED:
            slot = per_inst.setdefault(c["institution_id"], {"n": 0, "bureaus": set()})
            slot["n"] += 1
            slot["bureaus"].add(c["bureau_id"])
    top = sorted(per_inst.items(), key=lambda kv: (-kv[1]["n"], -len(kv[1]["bureaus"])))[:8]
    top_institutions = [{"institution": inst_by_id[i], "active": d["n"],
                         "bureaus": len(d["bureaus"])} for i, d in top]

    unlinked = sum(1 for c in rows if c["institution_id"] is None and c["status"] != DISPOSED)
    mine_bureau = user.get("bureau_id")
    my_open = [c for c in rows if c["bureau_id"] == mine_bureau and c["status"] != DISPOSED]
    needs = sorted((c for c in my_open if sla_service.state(c, now) in (OVERDUE, DUE_SOON)),
                   key=lambda c: c["expected_disposal_at"])[:6]

    return {
        "total": total, "active": active, "by_status": by_status,
        "overdue": sla[OVERDUE], "due_soon": sla[DUE_SOON],
        "alerts": len(alerts), "alert_rows": alerts[:5], "unlinked": unlinked,
        "bureau_rows": bureau_rows, "region_rows": region_rows,
        "top_institutions": top_institutions, "needs_attention": needs,
        "citizen_pending": sum(1 for c in rows if c.get("channel") == "citizen"
                               and c["status"] == PENDING),
        "status_segments": [{"label": s, "value": by_status[s], "color": STATUS_COLORS[s],
                             "href": f"/cases?status={s}"} for s in STATUSES],
        "generated_at": now,
    }

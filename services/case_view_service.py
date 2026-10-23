"""Read side: case lists, case detail, institution profile, as a given user may see them.

Everything returned here has already been through `access_service.present`, so
a template can render it without making any security decision of its own.
"""
from __future__ import annotations

import time

from db import bureaus, case_actions, case_links, complaints, documents, institutions, users
from domain import rbac
from domain.constants import (ACTIVE_STATUSES, CONFIRMED, DISPOSED, OVERLAPPING_PROCEEDING,
                              PENDING_REVIEW, STATUS_TRANSITIONS, SUPER_ADMIN, TRANSFERRED)
from services import access_service, sla_service
from services.errors import NotFound

FILTER_KEYS = ("institution", "aicte_id", "case_number", "bureau", "region", "category",
               "status", "officer", "date_from", "date_to", "deadline", "overlap", "q")


def _bureau_code(bid):
    b = bureaus.get(bid)
    return b["code"] if b else "?"


def overlap_flagged_ids() -> set:
    """Cases carrying an open or confirmed overlapping-proceeding link."""
    out = set()
    for l in case_links.all_rows():
        if l["relationship_type"] == OVERLAPPING_PROCEEDING and l["review_status"] in (
                PENDING_REVIEW, CONFIRMED):
            out.update((l["complaint_a_id"], l["complaint_b_id"]))
    return out


def _row(user, c, flagged, now):
    p = access_service.present(user, c)
    if p is None:
        return None
    inst = institutions.get(c["institution_id"]) if c["institution_id"] else None
    p["institution"] = inst
    p["institution_label"] = inst["name"] if inst else (
        (c["institution_name_stated"] + " (unlinked)") if p["access"] == "full"
        and c["institution_name_stated"] else "Unlinked institution")
    p["aicte_id"] = inst["aicte_permanent_id"] if inst else None
    p["region"] = inst["region"] if inst else None
    p["bureau_code"] = _bureau_code(c["bureau_id"])
    off = users.get_by_id(c["officer_id"]) if (p["access"] == "full" and c["officer_id"]) else None
    p["officer_name"] = off["display_name"] if off else None
    p["sla"] = sla_service.state(c, now)
    p["overlap"] = c["id"] in flagged
    return p


def _matches(row, f) -> bool:
    def has(text, needle):
        return needle.lower() in (text or "").lower()
    if f.get("institution") and not has(row["institution_label"], f["institution"]):
        return False
    if f.get("aicte_id") and not has(row["aicte_id"], f["aicte_id"]):
        return False
    if f.get("case_number") and not has(row["case_number"], f["case_number"]):
        return False
    if f.get("bureau") and str(row["bureau_id"]) != str(f["bureau"]) \
            and row["bureau_code"] != f["bureau"]:
        return False
    if f.get("region") and (row["region"] or "").lower() != f["region"].lower():
        return False
    if f.get("category") and row["category"] != f["category"]:
        return False
    if f.get("status") and row["status"] != f["status"]:
        return False
    if f.get("officer"):
        if row["restricted"] or str(row["officer_id"]) != str(f["officer"]):
            return False
    if f.get("date_from") and (row["received_at"] or 0) < f["date_from"]:
        return False
    if f.get("date_to") and (row["received_at"] or 0) > f["date_to"]:
        return False
    if f.get("deadline") and row["sla"] != f["deadline"]:
        return False
    if f.get("overlap") and not row["overlap"]:
        return False
    if f.get("q") and not (has(row["case_number"], f["q"]) or has(row["institution_label"], f["q"])
                           or (not row["restricted"] and (has(row["title"], f["q"])
                                                          or has(row["description"], f["q"])))):
        return False
    return True


def list_cases(user: dict, filters: dict | None = None, limit: int = 500) -> list:
    filters = {k: v for k, v in (filters or {}).items() if v not in (None, "")}
    now = time.time()
    flagged = overlap_flagged_ids()
    rows = []
    for c in complaints.all_rows():
        row = _row(user, c, flagged, now)
        if row and _matches(row, filters):
            rows.append(row)
    rows.sort(key=lambda r: (r["received_at"] or 0, r["id"]), reverse=True)
    return rows[:limit]


def _action_rows(c):
    out = []
    for a in case_actions.for_complaint(c["id"]):
        actor = users.get_by_id(a["actor_id"]) if a["actor_id"] else None
        out.append({**a, "actor_name": actor["display_name"] if actor else "System",
                    "bureau_code": _bureau_code(a["bureau_id"]) if a["bureau_id"] else None})
    return out


def case_detail(user: dict, case_number: str) -> dict:
    c = complaints.get_by_number(case_number)
    if not c or access_service.level(user, c) is None:
        raise NotFound("case not found")
    row = _row(user, c, overlap_flagged_ids(), time.time())
    full = row["access"] == "full"
    editable = access_service.can_edit(user, c)
    manage = access_service.can_manage_bureau(user, c["bureau_id"])
    transitions = [s for s in STATUS_TRANSITIONS.get(c["status"], [])] if editable else []

    linked = []
    for l in case_links.for_complaint(c["id"]):
        o = complaints.get(case_links.other_side(l, c["id"]))
        orow = _row(user, o, set(), time.time()) if o else None
        if not orow:
            continue
        # evidence (shared terms etc.) is only for people who may review the pair
        can_see_evidence = user["role"] == SUPER_ADMIN or full
        linked.append({"link": l, "other": orow,
                       "evidence": l["evidence"] if can_see_evidence else None})
    linked.sort(key=lambda x: -(x["link"]["score"] or 0))

    return {
        "case": row, "full": full,
        "institution": row["institution"],
        "history": _action_rows(c) if full else [],
        "documents": documents.for_complaint(c["id"]) if full else [],
        "linked": linked,
        "can_edit": editable,
        "can_assign": manage and c["status"] != DISPOSED
                      and rbac.has_permission(user["role"], rbac.COMPLAINT_ASSIGN),
        "can_route": editable and rbac.has_permission(user["role"], rbac.COMPLAINT_ESCALATE),
        "can_accept": c["status"] == TRANSFERRED and manage,
        "can_request": editable and rbac.has_permission(user["role"], rbac.COMPLAINT_REQUEST)
                       and not rbac.has_permission(user["role"], rbac.COMPLAINT_ESCALATE),
        "transitions": [s for s in transitions if s not in ("Transferred", "Escalated", "Pending")],
        "officers": [u for u in users.list_all(bureau_id=c["bureau_id"])
                     if u["role"] in ("officer", "regional_user") and u["is_active"]],
        "bureaus": [b for b in bureaus.list_all() if b["id"] != c["bureau_id"]],
        "alias_suggestions": [],
    }


def institution_profile(user: dict, aicte_id: str) -> dict:
    inst = institutions.get_by_aicte_id(aicte_id)
    if not inst:
        raise NotFound("institution not found")
    if user["role"] == "regional_user" and inst.get("region") != user.get("region"):
        # a regional user can still open an institution they already have a case at
        if not any(c["bureau_id"] == user.get("bureau_id")
                   for c in complaints.for_institution(inst["id"])):
            raise NotFound("institution not found")
    now, flagged = time.time(), overlap_flagged_ids()
    rows = [r for r in (_row(user, c, flagged, now) for c in complaints.for_institution(inst["id"]))
            if r]
    rows.sort(key=lambda r: (r["received_at"] or 0), reverse=True)
    active = [r for r in rows if r["status"] in ACTIVE_STATUSES]
    closed = [r for r in rows if r["status"] == DISPOSED]
    involved = sorted({r["bureau_code"] for r in active})

    history = []
    for r in rows:
        if r["access"] == "full":
            for a in _action_rows(r):
                history.append({**a, "case_number": r["case_number"]})
    history.sort(key=lambda a: (a["at"], a["id"]), reverse=True)

    ids = {r["id"] for r in rows}
    links = []
    for l in case_links.all_rows():
        if l["complaint_a_id"] in ids and l["complaint_b_id"] in ids \
                and l["relationship_type"] != "same_institution":
            a = next(r for r in rows if r["id"] == l["complaint_a_id"])
            b = next(r for r in rows if r["id"] == l["complaint_b_id"])
            if "full" in (a["access"], b["access"]):
                links.append({"link": l, "a": a, "b": b})
    return {"institution": inst, "active": active, "closed": closed, "bureaus": involved,
            "history": history[:100], "links": links,
            "aliases": institutions.list_aliases(inst["id"], status="approved")}

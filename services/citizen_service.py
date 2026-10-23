"""Citizen-facing read/write side.

A citizen sees ONLY their own complaints, and of those only what is meant for
the complainant: the facts they filed, a plain-language status, the whitelisted
progress steps in `CITIZEN_VISIBLE_ACTIONS`, messages officers addressed to them,
and their own evidence. Internal remarks, officer names, other bureaus' cases and
overlap links never reach this module's output.
"""
from __future__ import annotations

import re

from db import audit, bureaus, case_actions, complaints, documents, institutions, notifications, pool, users
from domain.constants import (CITIZEN, CITIZEN_VISIBLE_ACTIONS, DISPOSED, ESCALATED,
                              PENDING, TRANSFERRED, UNDER_PROCESS)
from services import auth_service, sla_service
from services.errors import NotFound, PermissionDenied, ValidationError

_USERNAME = re.compile(r"^[a-z0-9._-]{4,32}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# status -> (plain label, step index on the 4-step tracker, explanation)
STEPS = ["Received", "Under examination", "Action taken", "Closed"]
_PLAIN = {
    PENDING:       ("Received", 0, "Your complaint has been received and is waiting to be taken up."),
    UNDER_PROCESS: ("Under examination", 1, "An AICTE officer is examining your complaint."),
    TRANSFERRED:   ("Forwarded", 1, "Your complaint has been forwarded to the concerned AICTE bureau."),
    ESCALATED:     ("Under senior review", 2, "Your complaint has been escalated for senior review."),
    DISPOSED:      ("Closed", 3, "Your complaint has been disposed of."),
}


def register(username, display_name, password, contact) -> dict:
    username = (username or "").strip().lower()
    display_name = (display_name or "").strip()
    contact = (contact or "").strip()
    if not _USERNAME.match(username):
        raise ValidationError("username: 4-32 characters, letters, digits, . _ -")
    if len(display_name) < 2:
        raise ValidationError("please enter your name")
    if len(password or "") < 8:
        raise ValidationError("password must be at least 8 characters")
    if not _EMAIL.match(contact):
        raise ValidationError("enter a valid email address")
    if users.get_by_username(username):
        raise ValidationError("that username is taken")
    u = users.create(username, display_name, CITIZEN, auth_service.hash_pin(password),
                     contact=contact, created_by="self-registration")
    audit.add(username, "citizen.register", target_type="user", target_id=u["id"])
    return u


def _status_block(c: dict) -> dict:
    label, step, text = _PLAIN[c["status"]]
    return {"label": label, "step": step, "text": text, "steps": STEPS}


def _summary(c: dict) -> dict:
    inst = institutions.get(c["institution_id"]) if c["institution_id"] else None
    st = _status_block(c)
    from domain.constants import QUERY_CATEGORY
    return {"case_number": c["case_number"], "title": c["title"], "category": c["category"],
            "kind": "Question" if c["category"] == QUERY_CATEGORY else "Complaint",
            "institution_name": inst["name"] if inst else "Not yet identified",
            "aicte_id": inst["aicte_permanent_id"] if inst else None,
            "received_at": c["received_at"], "status": c["status"], "status_label": st["label"],
            "step": st["step"], "expected_by": c["expected_disposal_at"],
            "overdue": sla_service.state(c) == "overdue", "last_update": c["last_action_at"]}


def list_own(citizen: dict) -> list:
    rows = [_summary(c) for c in complaints.all_rows() if c.get("citizen_id") == citizen["id"]]
    rows.sort(key=lambda r: r["received_at"] or 0, reverse=True)
    return rows


def _own(citizen: dict, case_number: str) -> dict:
    c = complaints.get_by_number(case_number)
    if not c or c.get("citizen_id") != citizen["id"] or citizen["role"] != CITIZEN:
        raise NotFound("complaint not found")        # never confirm another case exists
    return c


def detail(citizen: dict, case_number: str) -> dict:
    c = _own(citizen, case_number)
    updates = []
    for a in case_actions.for_complaint(c["id"]):
        label = CITIZEN_VISIBLE_ACTIONS.get(a["action_type"])
        if a["action_type"] == "evidence_added":
            label = "You added supporting evidence"
        if not label:
            continue                                  # internal step: not shown
        note = None
        if a["action_type"] == "citizen_update":
            note = a["remarks"]                       # written for the complainant
        elif a["action_type"] == "disposed":
            note = None                               # the disposal remark is internal;
                                                      # officers send the outcome as an update
        elif a["action_type"] == "evidence_added":
            note = a["remarks"]
        updates.append({"at": a["at"], "label": label, "note": note})
    docs = [{"id": d["id"], "filename": d["filename"], "at": d["uploaded_at"]}
            for d in documents.for_complaint(c["id"]) if d["uploaded_by"] == citizen["id"]]
    resolution = next((u for u in reversed(updates) if u["label"] == "Update from AICTE"), None) \
        if c["status"] == DISPOSED else None
    return {"case": {**_summary(c), "description": c["description"]},
            "status": _status_block(c), "updates": updates, "documents": docs,
            "resolution": resolution, "can_add_evidence": c["status"] != DISPOSED}


def add_evidence(citizen: dict, case_number: str, filename: str, stored_name: str) -> dict:
    c = _own(citizen, case_number)
    if c["status"] == DISPOSED:
        raise ValidationError("this complaint is closed")
    with pool.transaction():
        doc = documents.add(c["id"], filename, stored_name, c["bureau_id"], citizen["id"])
        case_actions.add(c["id"], "evidence_added", actor_id=citizen["id"],
                         bureau_id=c["bureau_id"], remarks=filename, document_ids=[doc["id"]])
        complaints.update(c["id"], last_action_at=doc["uploaded_at"],
                          last_action="Evidence added")
        audit.add(citizen["username"], "complaint.evidence", target_type="complaint",
                  target_id=c["id"], detail={"filename": filename})
    return doc


def can_download(user: dict, doc: dict) -> bool:
    """Citizens download only their own uploads; officials need full access to the case."""
    c = complaints.get(doc["complaint_id"])
    if not c:
        return False
    if user["role"] == CITIZEN:
        return c.get("citizen_id") == user["id"] and doc["uploaded_by"] == user["id"]
    from services import access_service
    return access_service.level(user, c) == access_service.FULL


def institution_search(q: str, limit: int = 8) -> list:
    """Public registry fields only (name, AICTE ID, region), for the picker."""
    q = (q or "").strip().lower()
    if len(q) < 2:
        return []
    from services.institution_service import normalize_name
    qt = set(normalize_name(q))
    hits = []
    for i in institutions.list_all():
        hay = i["name"].lower()
        score = 0
        if q in hay or q == i["aicte_permanent_id"].lower():
            score = 3
        elif i["aicte_permanent_id"].lower().startswith(q):
            score = 2
        elif qt and qt <= set(normalize_name(i["name"])):
            score = 1
        if score:
            hits.append((score, i))
    hits.sort(key=lambda x: (-x[0], x[1]["name"]))
    return [{"aicte_id": i["aicte_permanent_id"], "name": i["name"],
             "region": i["region"], "address": i["address"]} for _s, i in hits[:limit]]

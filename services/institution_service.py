"""Institution registry logic: alias SUGGESTIONS for name-only complaints.

Hard rule: the AICTE permanent ID is the only automatic join key. A name that
merely looks like an institution never links a complaint to it. It produces an
alias *suggestion* that a human approves or rejects.
"""
from __future__ import annotations

import difflib
import re

from db import audit, case_actions, complaints, institutions
from domain import rbac
from domain.constants import DISPOSED
from services import access_service
from services.errors import NotFound, PermissionDenied, ValidationError

_ABBREV = {"engg": "engineering", "eng": "engineering", "coll": "college",
           "clg": "college", "inst": "institute", "instt": "institute",
           "univ": "university", "tech": "technology", "mgmt": "management",
           "mgt": "management", "&": "and", "dept": "department"}
_STOP = {"the", "of", "and", "for", "a", "an", "in"}
SUGGEST_MIN = 0.55


def normalize_name(name: str) -> list:
    toks = re.findall(r"[a-z0-9&]+", (name or "").lower())
    toks = [_ABBREV.get(t, t) for t in toks]
    return [t for t in toks if t not in _STOP]


def name_similarity(a: str, b: str) -> float:
    ta, tb = normalize_name(a), normalize_name(b)
    if not ta or not tb:
        return 0.0
    sa, sb = set(ta), set(tb)
    jaccard = len(sa & sb) / len(sa | sb)
    ratio = difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    return round(0.5 * jaccard + 0.5 * ratio, 3)


def suggest(name: str, limit: int = 3) -> list:
    """Ranked candidate institutions for a free-text name. Never auto-links."""
    out = []
    for inst in institutions.list_all():
        best = name_similarity(name, inst["name"])
        approved = False
        for al in institutions.list_aliases(inst["id"], status="approved"):
            s = name_similarity(name, al["alias"])
            if s >= 0.99:
                approved = True
            best = max(best, s)
        if best >= SUGGEST_MIN or approved:
            out.append({"institution": inst, "score": 1.0 if approved else best,
                        "previously_approved": approved})
    out.sort(key=lambda r: -r["score"])
    return out[:limit]


def record_suggestions(complaint: dict, stated_name: str) -> list:
    """Store alias suggestions for a complaint that named an institution with no ID."""
    made = []
    for s in suggest(stated_name):
        made.append(institutions.add_alias(s["institution"]["id"], stated_name,
                                           status="suggested", complaint_id=complaint["id"]))
    return made


def pending_suggestions(user: dict) -> list:
    """Suggestions this user is allowed to act on (own-bureau complaints, or all for super admin)."""
    rows = []
    for al in institutions.list_aliases(status="suggested"):
        c = complaints.get(al["complaint_id"]) if al["complaint_id"] else None
        if c is None or c["institution_id"] is not None:
            continue
        if not access_service.can_manage_bureau(user, c["bureau_id"]):
            continue
        rows.append({"alias": al, "complaint": c,
                     "institution": institutions.get(al["institution_id"])})
    return rows


def approve_alias(actor: dict, alias_id: int) -> dict:
    """Human approves an alias: the complaint is linked and overlap detection runs."""
    if not rbac.has_permission(actor["role"], rbac.ALIAS_REVIEW):
        raise PermissionDenied("not allowed to review aliases")
    al = institutions.get_alias(alias_id)
    if not al:
        raise NotFound("alias not found")
    c = complaints.get(al["complaint_id"]) if al["complaint_id"] else None
    if c and not access_service.can_manage_bureau(actor, c["bureau_id"]):
        raise PermissionDenied("this alias belongs to another bureau's complaint")
    inst = institutions.get(al["institution_id"])
    institutions.set_alias_status(alias_id, "approved")
    # any other open suggestions for the same complaint are now moot
    if c:
        for other in institutions.list_aliases(status="suggested"):
            if other["complaint_id"] == c["id"] and other["id"] != alias_id:
                institutions.delete_alias(other["id"])
        if c["institution_id"] is None and c["status"] != DISPOSED:
            complaints.update(c["id"], institution_id=inst["id"])
            case_actions.add(c["id"], "institution_linked", actor_id=actor["id"],
                             bureau_id=actor.get("bureau_id"), to_value=inst["aicte_permanent_id"],
                             remarks=f"Alias '{al['alias']}' approved for {inst['name']}")
    audit.add(actor["username"], "alias.approve", target_type="institution_alias",
              target_id=alias_id, detail={"institution": inst["aicte_permanent_id"],
                                          "alias": al["alias"],
                                          "complaint_id": c["id"] if c else None})
    if c:
        from services import complaint_service
        complaint_service.run_overlap_detection(c["id"])
    return institutions.get_alias(alias_id)


def reject_alias(actor: dict, alias_id: int) -> None:
    if not rbac.has_permission(actor["role"], rbac.ALIAS_REVIEW):
        raise PermissionDenied("not allowed to review aliases")
    al = institutions.get_alias(alias_id)
    if not al:
        raise NotFound("alias not found")
    c = complaints.get(al["complaint_id"]) if al["complaint_id"] else None
    if c and not access_service.can_manage_bureau(actor, c["bureau_id"]):
        raise PermissionDenied("this alias belongs to another bureau's complaint")
    institutions.delete_alias(alias_id)
    audit.add(actor["username"], "alias.reject", target_type="institution_alias",
              target_id=alias_id, detail={"alias": al["alias"]})


def create_institution(actor: dict, aicte_id, name, address=None, region=None) -> dict:
    if not rbac.has_permission(actor["role"], rbac.INSTITUTION_MANAGE):
        raise PermissionDenied("only a super admin manages the registry")
    try:
        inst = institutions.create(aicte_id, name, address=address, region=region)
    except ValueError as e:
        raise ValidationError(str(e)) from e
    audit.add(actor["username"], "institution.create", target_type="institution",
              target_id=inst["id"], detail={"aicte_id": inst["aicte_permanent_id"]})
    return inst

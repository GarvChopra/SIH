"""Per-case visibility and edit rules. The single place these are decided.

Visibility
  * Anyone signed in can see THAT a case exists at an institution: bureau,
    category, status and dates ("summary").
  * Title, description, remarks, documents and history ("full") are visible only
    to: super_admin; users of the owning bureau; users of a bureau that has a
    CONFIRMED overlap with this case.
  * A regional_user is further limited to institutions in their own region.

Editing is a separate, narrower question. Viewing never implies editing, and a
confirmed overlap grants read access only.

All of this runs in the service layer; templates only render what they are given.
"""
from __future__ import annotations

from db import case_links, complaints, institutions
from domain.constants import (BUREAU_ADMIN, CONFIRMED, DISPOSED, OFFICER,
                              REGIONAL_USER, SUPER_ADMIN)

FULL, SUMMARY = "full", "summary"

_SUMMARY_FIELDS = ("id", "case_number", "institution_id", "category", "status",
                   "bureau_id", "received_at", "expected_disposal_at")
RESTRICTED_TITLE = "Restricted – another bureau's case"


def in_scope(user: dict, c: dict) -> bool:
    """May this user know the case exists at all?"""
    if user["role"] != REGIONAL_USER:
        return True
    if c["bureau_id"] == user.get("bureau_id"):
        return True
    inst = institutions.get(c["institution_id"]) if c["institution_id"] else None
    return bool(inst and inst.get("region") and inst["region"] == user.get("region"))


def _confirmed_bureaus(cid: int) -> set:
    out = set()
    for l in case_links.for_complaint(cid):
        if l["review_status"] == CONFIRMED:
            other = complaints.get(case_links.other_side(l, cid))
            if other:
                out.add(other["bureau_id"])
    return out


def level(user: dict, c: dict):
    """FULL, SUMMARY, or None (not visible at all)."""
    if user["role"] == "citizen" or not in_scope(user, c):
        return None             # citizens use citizen_service, never the official views
    if user["role"] == SUPER_ADMIN:
        return FULL
    if c["bureau_id"] == user.get("bureau_id"):
        return FULL
    if user.get("bureau_id") in _confirmed_bureaus(c["id"]):
        return FULL
    return SUMMARY


def can_edit(user: dict, c: dict) -> bool:
    if c["status"] == DISPOSED and user["role"] not in (SUPER_ADMIN, BUREAU_ADMIN):
        return False
    if user["role"] == SUPER_ADMIN:
        return True
    if c["bureau_id"] != user.get("bureau_id"):
        return False
    if user["role"] == BUREAU_ADMIN:
        return True
    if user["role"] in (OFFICER, REGIONAL_USER):
        return c["officer_id"] == user["id"]
    return False


def can_manage_bureau(user: dict, bureau_id) -> bool:
    """Bureau-admin level authority over a bureau's cases."""
    return user["role"] == SUPER_ADMIN or (
        user["role"] == BUREAU_ADMIN and user.get("bureau_id") == bureau_id)


def present(user: dict, c: dict) -> dict:
    """The complaint as this user is allowed to see it."""
    lv = level(user, c)
    if lv is None:
        return None
    if lv == FULL:
        return {**c, "access": FULL, "restricted": False}
    masked = {k: c[k] for k in _SUMMARY_FIELDS}
    masked.update(title=RESTRICTED_TITLE, description=None, officer_id=None,
                  priority=None, last_action=None, last_action_at=None, next_action=None,
                  institution_name_stated=None, created_by=None, access=SUMMARY,
                  restricted=True)
    return masked

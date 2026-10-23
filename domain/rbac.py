"""Role-based access control. Stdlib + flask only (decorator).

Permissions say what a role may *do*. Which individual case a user may *see* or
*edit* is decided per-case in `services/access_service.py`, because that depends
on bureau, region and confirmed overlaps, not on role alone.
"""
from __future__ import annotations

import functools

from domain.constants import BUREAU_ADMIN, CITIZEN, OFFICER, REGIONAL_USER, SUPER_ADMIN

COMPLAINT_CREATE   = "complaint.create"
COMPLAINT_ASSIGN   = "complaint.assign"
COMPLAINT_UPDATE   = "complaint.update"       # remark / document / status on a case you own
COMPLAINT_ESCALATE = "complaint.escalate"     # execute transfer / escalate (officers only *request*)
COMPLAINT_REQUEST  = "complaint.request"      # ask a bureau admin to transfer / escalate
OVERLAP_REVIEW     = "overlap.review"
INSTITUTION_MANAGE = "institution.manage"
ALIAS_REVIEW       = "alias.review"
USER_MANAGE        = "user.manage"
BUREAU_MANAGE      = "bureau.manage"
AUDIT_VIEW         = "audit.view"
REPORT_VIEW        = "report.view"
CITIZEN_COMPLAIN   = "citizen.complain"

_FIELD = {COMPLAINT_CREATE, COMPLAINT_UPDATE, COMPLAINT_REQUEST, REPORT_VIEW}
_BUREAU_ADMIN = _FIELD | {COMPLAINT_ASSIGN, COMPLAINT_ESCALATE, OVERLAP_REVIEW, ALIAS_REVIEW}
_SUPER = _BUREAU_ADMIN | {INSTITUTION_MANAGE, USER_MANAGE, BUREAU_MANAGE, AUDIT_VIEW}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    SUPER_ADMIN: set(_SUPER),
    BUREAU_ADMIN: set(_BUREAU_ADMIN),
    OFFICER: set(_FIELD),
    REGIONAL_USER: set(_FIELD),
    CITIZEN: {CITIZEN_COMPLAIN},
}


def has_permission(role, perm: str) -> bool:
    return bool(role) and perm in ROLE_PERMISSIONS.get(role, set())


def require_permission(perm: str):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            from flask import g, jsonify, redirect, request
            user = g.get("current_user")
            if user and has_permission(user["role"], perm):
                return fn(*args, **kwargs)
            if not user:
                if request.method != "GET" or request.path.endswith("/data"):
                    return jsonify({"error": "unauthenticated"}), 401
                return redirect("/login")
            return jsonify({"error": "forbidden", "need": perm}), 403
        return wrapper
    return deco

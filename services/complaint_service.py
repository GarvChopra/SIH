"""Case lifecycle: create, assign, transfer, escalate, status changes, remarks.

Rules this module owns
  * Every state change writes its `case_actions` row (and audit row) in the SAME
    transaction as the change itself. History is append-only.
  * Transitions follow `STATUS_TRANSITIONS`. Under Process needs an assigned
    officer; Disposed needs a remark; reopening needs a reason.
  * Editing rights come from `access_service.can_edit`. Officers can only
    *request* a transfer/escalation; bureau admins execute it.
  * AI never changes a case. Overlap detection only raises links for humans.
"""
from __future__ import annotations

import logging
import time

from db import audit, bureaus, case_actions, complaints, documents, institutions, notifications, pool, users
from domain import rbac
from domain.constants import (BUREAU_ADMIN, CATEGORIES, CITIZEN_VISIBLE_ACTIONS, DISPOSED,
                              ESCALATED, INTAKE_BUREAU_CODE, OFFICER, PENDING,
                              PRIORITIES, REGIONAL_USER, STATUS_TRANSITIONS, SUPER_ADMIN,
                              TRANSFERRED, UNDER_PROCESS)
from services import access_service, institution_service, sla_service
from services.errors import NotFound, PermissionDenied, ValidationError

log = logging.getLogger("samanvay.complaints")

NEXT_ACTION = {
    PENDING: "Assign an officer and begin examination",
    UNDER_PROCESS: "Complete examination and dispose, or escalate",
    TRANSFERRED: "Receiving bureau to accept the transfer",
    ESCALATED: "Senior review; resume processing or dispose",
    DISPOSED: None,
}
_MIN_TEXT = 5


# ── helpers ────────────────────────────────────────────────────────────────

def _load(ref) -> dict:
    c = complaints.get(ref) if isinstance(ref, int) else complaints.get_by_number(str(ref))
    if not c:
        raise NotFound("case not found")
    return c


def _load_visible(actor: dict, ref) -> dict:
    c = _load(ref)
    if access_service.level(actor, c) is None:
        raise NotFound("case not found")      # do not reveal it exists
    return c


def _require_edit(actor: dict, c: dict) -> None:
    if not access_service.can_edit(actor, c):
        raise PermissionDenied("you cannot modify this case")


def _require_perm(actor: dict, perm: str) -> None:
    if not rbac.has_permission(actor["role"], perm):
        raise PermissionDenied("your role cannot do this")


def _record(c: dict, action_type: str, actor: dict, *, from_value=None, to_value=None,
            remarks=None, document_ids=None, bureau_id=None, **changes) -> dict:
    """History row + denormalised 'last action' fields on the case. Call inside a
    pool.transaction() together with the status change it describes."""
    now = time.time()
    case_actions.add(c["id"], action_type, actor_id=actor["id"],
                     bureau_id=bureau_id if bureau_id is not None else actor.get("bureau_id"),
                     from_value=from_value, to_value=to_value, remarks=remarks,
                     document_ids=document_ids, at=now)
    status = changes.get("status", c["status"])
    label = action_type.replace("_", " ").capitalize()
    updated = complaints.update(c["id"], last_action_at=now, last_action=label,
                                next_action=NEXT_ACTION.get(status), **changes)
    # The complainant hears about every citizen-visible step, in neutral wording.
    if c.get("citizen_id") and action_type in CITIZEN_VISIBLE_ACTIONS and action_type != "registered":
        text = CITIZEN_VISIBLE_ACTIONS[action_type]
        if action_type == "citizen_update" and remarks:
            text = f"{text}: {remarks}"
        notifications.add(c["citizen_id"], "case_update",
                          f"{c['case_number']}: {text}", complaint_id=c["id"])
    return updated


def _bureau_admins(bureau_id) -> list:
    return [u for u in users.list_all(bureau_id=bureau_id)
            if u["role"] == BUREAU_ADMIN and u["is_active"]]


def _super_admins() -> list:
    return [u for u in users.list_all(role=SUPER_ADMIN) if u["is_active"]]


def _notify(recipients, type_, message, c, exclude_id=None) -> None:
    seen = set()
    for u in recipients:
        if u and u["id"] != exclude_id and u["id"] not in seen:
            seen.add(u["id"])
            notifications.add(u["id"], type_, message, complaint_id=c["id"])


def run_overlap_detection(cid: int) -> None:
    """Hook into the overlap service. Never raises: a failed AI call must not
    block case registration."""
    try:
        from services import overlap_service
    except ImportError:          # overlap service not installed in this build
        return
    overlap_service.detect_safely(cid)


# ── create ─────────────────────────────────────────────────────────────────

def create(actor: dict, *, title, description, category, priority="medium", aicte_id=None,
           institution_name=None, bureau_id=None, received_at=None) -> dict:
    _require_perm(actor, rbac.COMPLAINT_CREATE)
    title, description = (title or "").strip(), (description or "").strip()
    if len(title) < _MIN_TEXT or len(description) < _MIN_TEXT:
        raise ValidationError("title and description are required")
    if category not in CATEGORIES:
        raise ValidationError("unknown category")
    if priority not in PRIORITIES:
        raise ValidationError("unknown priority")

    if actor["role"] == SUPER_ADMIN:
        bid = bureau_id
        if bid is None or not bureaus.get(bid):
            raise ValidationError("choose the bureau that owns this case")
    else:
        bid = actor.get("bureau_id")

    inst = None
    aicte_id = (aicte_id or "").strip()
    if aicte_id:
        inst = institutions.get_by_aicte_id(aicte_id)
        if not inst:
            raise ValidationError(f"no institution with AICTE ID {aicte_id!r}")
    elif not (institution_name or "").strip():
        raise ValidationError("give the AICTE permanent ID, or at least the institution name")
    if actor["role"] == REGIONAL_USER and inst and inst.get("region") != actor.get("region"):
        raise PermissionDenied("this institution is outside your region")

    received = float(received_at) if received_at else time.time()
    with pool.transaction():
        c = complaints.insert(
            institution_id=inst["id"] if inst else None,
            institution_name_stated=(institution_name or "").strip() or None,
            title=title, description=description, category=category, priority=priority,
            bureau_id=bid, received_at=received,
            expected_disposal_at=sla_service.disposal_deadline(category, received),
            created_by=actor["id"])
        c = _record(c, "registered", actor, to_value=PENDING, bureau_id=bid,
                    remarks=None if inst else
                    "Institution given by name only; awaiting alias approval")
        audit.add(actor["username"], "complaint.create", target_type="complaint",
                  target_id=c["id"], detail={"case_number": c["case_number"], "bureau_id": bid})
    if inst is None:
        institution_service.record_suggestions(c, institution_name)
    else:
        run_overlap_detection(c["id"])
    return complaints.get(c["id"])


# ── assignment ─────────────────────────────────────────────────────────────

def assign(actor: dict, ref, officer_id: int) -> dict:
    _require_perm(actor, rbac.COMPLAINT_ASSIGN)
    c = _load_visible(actor, ref)
    if not access_service.can_manage_bureau(actor, c["bureau_id"]):
        raise PermissionDenied("you can only assign officers in your own bureau")
    if c["status"] == DISPOSED:
        raise ValidationError("a disposed case cannot be assigned; reopen it first")
    off = users.get_by_id(officer_id)
    if not off or not off["is_active"] or off["role"] not in (OFFICER, REGIONAL_USER):
        raise ValidationError("choose an active officer")
    if off["bureau_id"] != c["bureau_id"]:
        raise ValidationError("the officer must belong to the case's bureau")
    prev = users.get_by_id(c["officer_id"]) if c["officer_id"] else None
    with pool.transaction():
        c = _record(c, "assigned", actor, from_value=prev["display_name"] if prev else None,
                    to_value=off["display_name"], officer_id=off["id"])
        audit.add(actor["username"], "complaint.assign", target_type="complaint",
                  target_id=c["id"], detail={"officer": off["username"]})
        notifications.add(off["id"], "assignment",
                          f"{c['case_number']} has been assigned to you.", complaint_id=c["id"])
    return c


# ── status ─────────────────────────────────────────────────────────────────

def _check_transition(c: dict, to: str) -> None:
    if to not in STATUS_TRANSITIONS.get(c["status"], []):
        raise ValidationError(f"cannot move a case from {c['status']} to {to}")


def change_status(actor: dict, ref, to: str, remarks: str | None = None,
                  citizen_message: str | None = None) -> dict:
    """Under Process / Disposed / resume-from-escalation. Transfer, acceptance and
    escalation have dedicated functions because they need extra input."""
    _require_perm(actor, rbac.COMPLAINT_UPDATE)
    c = _load_visible(actor, ref)
    _require_edit(actor, c)
    remarks = (remarks or "").strip() or None
    if to in (TRANSFERRED, ESCALATED, PENDING):
        raise ValidationError("use the transfer / escalate / accept actions for that change")
    _check_transition(c, to)
    changes = {}
    if to == UNDER_PROCESS:
        if not c["officer_id"]:
            raise ValidationError("assign an officer before moving a case to Under Process")
        if c["status"] == DISPOSED and not remarks:
            raise ValidationError("a reason is required to reopen a disposed case")
        action = "reopened" if c["status"] == DISPOSED else "status_changed"
    elif to == DISPOSED:
        if not remarks:
            raise ValidationError("a disposal remark is required")
        if c.get("citizen_id") and not (citizen_message or "").strip():
            raise ValidationError("write the outcome message the complainant will see")
        action = "disposed"
    else:
        raise ValidationError("unsupported status")
    with pool.transaction():
        prev = c["status"]
        c = _record(c, action, actor, from_value=prev, to_value=to, remarks=remarks,
                    status=to, **changes)
        audit.add(actor["username"], "complaint.status", target_type="complaint",
                  target_id=c["id"], detail={"from": prev, "to": to})
        msg = (citizen_message or "").strip()
        if msg and c.get("citizen_id"):
            c = _record(c, "citizen_update", actor, remarks=msg)
    return c


def escalate(actor: dict, ref, remarks: str) -> dict:
    _require_perm(actor, rbac.COMPLAINT_ESCALATE)
    c = _load_visible(actor, ref)
    _require_edit(actor, c)
    remarks = (remarks or "").strip()
    if not remarks:
        raise ValidationError("say why this case is being escalated")
    _check_transition(c, ESCALATED)
    with pool.transaction():
        prev = c["status"]
        c = _record(c, "escalated", actor, from_value=prev, to_value=ESCALATED,
                    remarks=remarks, status=ESCALATED)
        audit.add(actor["username"], "complaint.escalate", target_type="complaint",
                  target_id=c["id"], detail={"from": prev})
        _notify(_super_admins() + _bureau_admins(c["bureau_id"]), "escalation",
                f"{c['case_number']} has been escalated: {remarks}", c, exclude_id=actor["id"])
    return c


def transfer(actor: dict, ref, to_bureau_id: int, remarks: str = "") -> dict:
    """Ownership moves to the receiving bureau immediately; the receiving bureau
    then accepts (Transferred -> Pending)."""
    _require_perm(actor, rbac.COMPLAINT_ESCALATE)
    c = _load_visible(actor, ref)
    _require_edit(actor, c)
    target = bureaus.get(to_bureau_id)
    if not target:
        raise ValidationError("choose a receiving bureau")
    if target["id"] == c["bureau_id"]:
        raise ValidationError("the case already belongs to that bureau")
    _check_transition(c, TRANSFERRED)
    src = bureaus.get(c["bureau_id"])
    with pool.transaction():
        prev = c["status"]
        c = _record(c, "transferred", actor, from_value=src["code"], to_value=target["code"],
                    remarks=(remarks or "").strip() or None, status=TRANSFERRED,
                    bureau_id=src["id"], **{"officer_id": None})
        # the receiving bureau owns it from here
        c = complaints.update(c["id"], bureau_id=target["id"])
        audit.add(actor["username"], "complaint.transfer", target_type="complaint",
                  target_id=c["id"], detail={"from": src["code"], "to": target["code"],
                                             "prev_status": prev})
        _notify(_bureau_admins(target["id"]), "transfer",
                f"{c['case_number']} was transferred to {target['code']} from {src['code']}.",
                c, exclude_id=actor["id"])
    return c


def accept_transfer(actor: dict, ref) -> dict:
    _require_perm(actor, rbac.COMPLAINT_ESCALATE)
    c = _load_visible(actor, ref)
    if c["status"] != TRANSFERRED:
        raise ValidationError("this case is not awaiting acceptance")
    if not access_service.can_manage_bureau(actor, c["bureau_id"]):
        raise PermissionDenied("only the receiving bureau can accept a transfer")
    with pool.transaction():
        c = _record(c, "transfer_accepted", actor, from_value=TRANSFERRED, to_value=PENDING,
                    status=PENDING)
        audit.add(actor["username"], "complaint.accept_transfer", target_type="complaint",
                  target_id=c["id"])
    return c


def request_action(actor: dict, ref, kind: str, remarks: str) -> dict:
    """An officer asks a bureau admin to transfer or escalate. No status change."""
    _require_perm(actor, rbac.COMPLAINT_REQUEST)
    if kind not in ("transfer", "escalation"):
        raise ValidationError("unknown request type")
    c = _load_visible(actor, ref)
    _require_edit(actor, c)
    remarks = (remarks or "").strip()
    if not remarks:
        raise ValidationError("say what you are asking for and why")
    with pool.transaction():
        case_actions.add(c["id"], f"{kind}_requested", actor_id=actor["id"],
                         bureau_id=actor.get("bureau_id"), remarks=remarks)
        audit.add(actor["username"], f"complaint.{kind}_request", target_type="complaint",
                  target_id=c["id"])
        _notify(_bureau_admins(c["bureau_id"]), f"{kind}_request",
                f"{actor['display_name']} requests {kind} of {c['case_number']}: {remarks}",
                c, exclude_id=actor["id"])
    return c


# ── remarks, documents, text ───────────────────────────────────────────────

def add_remark(actor: dict, ref, text: str) -> dict:
    _require_perm(actor, rbac.COMPLAINT_UPDATE)
    c = _load_visible(actor, ref)
    _require_edit(actor, c)
    text = (text or "").strip()
    if not text:
        raise ValidationError("a remark cannot be empty")
    with pool.transaction():
        c = _record(c, "remark", actor, remarks=text)
    return c


def add_document(actor: dict, ref, filename: str, stored_name: str) -> dict:
    _require_perm(actor, rbac.COMPLAINT_UPDATE)
    c = _load_visible(actor, ref)
    _require_edit(actor, c)
    with pool.transaction():
        doc = documents.add(c["id"], filename, stored_name, c["bureau_id"], actor["id"])
        c = _record(c, "document_added", actor, remarks=filename, document_ids=[doc["id"]])
        audit.add(actor["username"], "complaint.document", target_type="complaint",
                  target_id=c["id"], detail={"filename": filename})
    return c


def update_text(actor: dict, ref, title: str, description: str) -> dict:
    """Edit title/description. Bumps `text_version`, which makes overlap detection
    re-evaluate (and is the only thing that re-raises a dismissed pair)."""
    _require_perm(actor, rbac.COMPLAINT_UPDATE)
    c = _load_visible(actor, ref)
    _require_edit(actor, c)
    title, description = (title or "").strip(), (description or "").strip()
    if len(title) < _MIN_TEXT or len(description) < _MIN_TEXT:
        raise ValidationError("title and description are required")
    if title == c["title"] and description == c["description"]:
        return c
    with pool.transaction():
        c = _record(c, "edited", actor, remarks="Title/description edited",
                    title=title, description=description,
                    text_version=(c.get("text_version") or 1) + 1)
        audit.add(actor["username"], "complaint.edit", target_type="complaint", target_id=c["id"])
    run_overlap_detection(c["id"])
    return complaints.get(c["id"])


# ── citizen channel ────────────────────────────────────────────────────────

def create_for_citizen(citizen: dict, *, aicte_id, category, title, description) -> dict:
    """A citizen files against an institution picked from the registry. The case is
    received by the intake bureau (PGRC), which can transfer it onward."""
    if not rbac.has_permission(citizen["role"], rbac.CITIZEN_COMPLAIN):
        raise PermissionDenied("only registered complainants can file here")
    title, description = (title or "").strip(), (description or "").strip()
    if len(title) < 8:
        raise ValidationError("give your complaint a short title (at least 8 characters)")
    if len(description) < 30:
        raise ValidationError("please describe the problem in at least 30 characters")
    if len(title) > 160 or len(description) > 4000:
        raise ValidationError("title or description is too long")
    if category not in CATEGORIES:
        raise ValidationError("choose a category")
    inst = institutions.get_by_aicte_id(aicte_id)
    if not inst:
        raise ValidationError("choose an institution from the search results")
    intake = bureaus.get_by_code(INTAKE_BUREAU_CODE)
    received = time.time()
    with pool.transaction():
        c = complaints.insert(
            institution_id=inst["id"], title=title, description=description, category=category,
            priority="medium", bureau_id=intake["id"], citizen_id=citizen["id"],
            channel="citizen", received_at=received,
            expected_disposal_at=sla_service.disposal_deadline(category, received),
            created_by=citizen["id"])
        c = _record(c, "registered", citizen, to_value=PENDING, bureau_id=intake["id"],
                    remarks="Filed by a citizen through the complaint portal")
        audit.add(citizen["username"], "complaint.create", target_type="complaint",
                  target_id=c["id"], detail={"case_number": c["case_number"], "channel": "citizen"})
        _notify(_bureau_admins(intake["id"]), "new_complaint",
                f"New citizen complaint {c['case_number']} against {inst['name']}.", c)
    run_overlap_detection(c["id"])
    return complaints.get(c["id"])


def post_citizen_update(actor: dict, ref, message: str) -> dict:
    """Officer-written message the complainant will see. The only free text that does."""
    _require_perm(actor, rbac.COMPLAINT_UPDATE)
    c = _load_visible(actor, ref)
    _require_edit(actor, c)
    if not c.get("citizen_id"):
        raise ValidationError("this case was not filed by a citizen")
    message = (message or "").strip()
    if not message:
        raise ValidationError("write the message for the complainant")
    with pool.transaction():
        c = _record(c, "citizen_update", actor, remarks=message)
    return c

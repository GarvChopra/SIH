"""Cross-bureau overlap detection and human review.

Detection (runs when a complaint is created, linked to an institution, or its
title/description changes):
  1. candidates = other NON-disposed complaints at the SAME institution_id
  2. score each pair: semantic (Cohere, optional) and lexical (always)
  3. classify: overlapping_proceeding / possible_duplicate / related / same_institution
  4. store evidence, raise an alert for overlapping_proceeding only

AI never merges, transfers or disposes of anything. It raises links; officials
confirm, dismiss, or mark them related-but-distinct, with a recorded reason.
"""
from __future__ import annotations

import logging
import time

from db import (audit, bureaus, case_actions, case_links, complaints, embeddings, institutions,
                notifications, pool, users)
from domain import rbac
from domain.constants import (BUREAU_ADMIN, CONFIRMED, CROSS_CATEGORY_MARGIN, DISMISSED, DISPOSED, EMBED_HIGH,
                              EMBED_MEDIUM, EMBED_MODEL, LEXICAL_HIGH, LEXICAL_MEDIUM,
                              OVERLAPPING_PROCEEDING, PENDING_REVIEW, POSSIBLE_DUPLICATE,
                              RELATED, RELATED_DISTINCT, REVIEW_DECISIONS, SAME_INSTITUTION,
                              SUPER_ADMIN)
from domain.constants import LLM_HIGH, LLM_MEDIUM, LLM_PREFILTER
from services import access_service, embedding_service, lexical, llm_similarity
from services.errors import NotFound, PermissionDenied, ValidationError

_AI_ENABLED = True


class ai_disabled:
    """Context manager: bulk jobs (the demo seeder) must not call a paid/rate-limited API
    for hundreds of pairs. Detection still runs, on the offline matcher."""
    def __enter__(self):
        global _AI_ENABLED
        self._prev, _AI_ENABLED = _AI_ENABLED, False

    def __exit__(self, *exc):
        global _AI_ENABLED
        _AI_ENABLED = self._prev

log = logging.getLogger("samanvay.overlap")

REVIEWABLE = (OVERLAPPING_PROCEEDING, POSSIBLE_DUPLICATE)
ALERT_MESSAGE = ("Both complaints concern AICTE institution {aicte_id} and describe similar "
                 "{category} allegations. They are assigned to {bureau_a} and {bureau_b}.")


# ── scoring ────────────────────────────────────────────────────────────────

def text_of(c: dict) -> str:
    return f"{c['title']}. {c['description']}"


def thresholds(mode: str):
    if mode == "embedding":
        return EMBED_HIGH, EMBED_MEDIUM
    if mode == "llm":
        return LLM_HIGH, LLM_MEDIUM
    return LEXICAL_HIGH, LEXICAL_MEDIUM


def classify(score: float, mode: str, same_bureau: bool, both_active: bool = True,
             same_category: bool = True) -> str:
    high, medium = thresholds(mode)
    if not same_category:
        high = min(0.99, high + CROSS_CATEGORY_MARGIN[mode])
    if score >= high:
        if not same_bureau and both_active:
            return OVERLAPPING_PROCEEDING
        return POSSIBLE_DUPLICATE
    if score >= medium:
        return RELATED
    return SAME_INSTITUTION


def _vector_for(c: dict):
    """Stored embedding for the complaint's current text, computing it once if possible."""
    row = embeddings.get(c["id"], c["text_version"])
    if row:
        return row["vector"]
    vec = embedding_service.embed(text_of(c), complaint_id=c["id"])
    if vec is not None:
        embeddings.store(c["id"], EMBED_MODEL, vec, c["text_version"])
    return vec


def score_pair(a: dict, b: dict) -> dict:
    lex, shared = lexical.similarity(text_of(a), text_of(b))
    semantic, mode, reason = None, "lexical", None
    if _AI_ENABLED and embedding_service.available():
        va, vb = _vector_for(a), _vector_for(b)
        if va is not None and vb is not None:
            semantic = round(embedding_service.cosine(va, vb), 4)
            mode = "embedding"
            audit.add("system", "ai.similarity", target_type="case_pair",
                      target_id=f"{a['id']}-{b['id']}",
                      detail={"model": EMBED_MODEL, "score": semantic, "success": True})
    elif _AI_ENABLED and llm_similarity.available() and lex >= LLM_PREFILTER:
        verdict = llm_similarity.judge(text_of(a), text_of(b), pair_id=f"{a['id']}-{b['id']}")
        if verdict is not None:
            semantic, mode, reason = verdict["score"], "llm", verdict["reason"]
    return {"lexical": lex, "semantic": semantic, "mode": mode, "ai_reason": reason,
            "score": semantic if semantic is not None else lex, "shared_terms": shared}


def _signature(a: dict, b: dict) -> str:
    lo, hi = (a, b) if a["id"] < b["id"] else (b, a)
    return f"{lo['id']}:{lo['text_version']}|{hi['id']}:{hi['text_version']}"


def _evidence(a: dict, b: dict, s: dict, rel: str) -> dict:
    inst = institutions.get(a["institution_id"])
    ba, bb = bureaus.get(a["bureau_id"]), bureaus.get(b["bureau_id"])
    same_cat = a["category"] == b["category"]
    ev = {
        "complaint_ids": [a["id"], b["id"]],
        "case_numbers": [a["case_number"], b["case_number"]],
        "institution_id": inst["id"], "aicte_id": inst["aicte_permanent_id"],
        "scores": {"lexical": s["lexical"], "semantic": s["semantic"], "used": s["score"],
                   "mode": s["mode"]},
        "shared_terms": s["shared_terms"], "ai_reason": s.get("ai_reason"),
        "categories": [a["category"], b["category"]], "same_category": same_cat,
        "bureaus": [ba["code"], bb["code"]], "bureau_ids": [a["bureau_id"], b["bureau_id"]],
        "statuses": [a["status"], b["status"]],
    }
    ev["message"] = ALERT_MESSAGE.format(
        aicte_id=inst["aicte_permanent_id"],
        category=a["category"].lower() if same_cat else "overlapping",
        bureau_a=ba["code"], bureau_b=bb["code"])
    return ev


# ── detection ──────────────────────────────────────────────────────────────

def detect(cid: int) -> list:
    """Compare one complaint with every other active complaint at its institution."""
    c = complaints.get(cid)
    if not c or not c["institution_id"] or c["status"] == DISPOSED or c.get("tier") == 1:
        return []                     # a Tier 1 query is a question, not a proceeding
    out = []
    for o in complaints.for_institution(c["institution_id"]):
        if o["id"] == c["id"] or o["status"] == DISPOSED or o.get("tier") == 1:
            continue
        link = _evaluate(c, o)
        if link:
            out.append(link)
    return out


def detect_safely(cid: int) -> None:
    """Never lets an AI/matching failure block case registration."""
    try:
        detect(cid)
    except Exception as e:  # noqa: BLE001
        log.exception("overlap detection failed for complaint %s", cid)
        try:
            audit.add("system", "overlap.error", target_type="complaint", target_id=cid,
                      detail={"error": f"{type(e).__name__}: {e}"[:200]})
        except Exception:  # noqa: BLE001
            pass


def _evaluate(c: dict, o: dict):
    a, b = (c, o) if c["id"] < o["id"] else (o, c)
    s = score_pair(a, b)
    rel = classify(s["score"], s["mode"], a["bureau_id"] == b["bureau_id"],
                   same_category=a["category"] == b["category"])
    sig = _signature(a, b)
    existing = case_links.get_pair(a["id"], b["id"])

    if existing and existing["review_status"] != PENDING_REVIEW:
        if existing["text_signature"] == sig:
            return existing                       # decided, nothing changed: never re-raised
        if existing["review_status"] == CONFIRMED:
            return existing                       # a confirmed link is a recorded decision
        reset = True                              # dismissed / related-distinct, but text changed
    else:
        reset = False

    ev = _evidence(a, b, s, rel)
    fields = dict(relationship_type=rel, score=s["score"], evidence=ev, text_signature=sig)
    if reset:
        fields.update(review_status=PENDING_REVIEW, reviewed_by=None, reviewed_at=None, reason=None)
    link = case_links.upsert(a["id"], b["id"], **fields)

    was_alert = bool(existing) and existing["relationship_type"] == OVERLAPPING_PROCEEDING \
        and not reset
    if rel == OVERLAPPING_PROCEEDING and not was_alert:
        _raise_alert(a, b, link, ev)
    return link


def _raise_alert(a: dict, b: dict, link: dict, ev: dict) -> None:
    with pool.transaction():
        for x in (a, b):
            case_actions.add(x["id"], "overlap_flagged", bureau_id=x["bureau_id"],
                             remarks=f"Possible overlapping proceeding: {ev['message']}",
                             to_value=link["relationship_type"])
        audit.add("system", "overlap.flag", target_type="case_link", target_id=link["id"],
                  detail={"cases": ev["case_numbers"], "score": ev["scores"]["used"],
                          "mode": ev["scores"]["mode"]})
        recipients = {}
        for x in (a, b):
            for u in users.list_all(bureau_id=x["bureau_id"]):
                if u["is_active"] and u["role"] == BUREAU_ADMIN:
                    recipients[u["id"]] = u
            if x["officer_id"]:
                recipients[x["officer_id"]] = users.get_by_id(x["officer_id"])
        for u in users.list_all(role=SUPER_ADMIN):
            if u["is_active"]:
                recipients[u["id"]] = u
        msg = (f"Overlap alert: {ev['case_numbers'][0]} ({ev['bureaus'][0]}) and "
               f"{ev['case_numbers'][1]} ({ev['bureaus'][1]}) may be the same issue at the same "
               f"institution. Review needed.")
        for u in recipients.values():
            notifications.add(u["id"], "overlap", msg, complaint_id=a["id"])


# ── review ─────────────────────────────────────────────────────────────────

def _involved(user: dict, a: dict, b: dict) -> bool:
    return user["role"] == SUPER_ADMIN or user.get("bureau_id") in (a["bureau_id"], b["bureau_id"])


def _active_pair(link):
    a, b = complaints.get(link["complaint_a_id"]), complaints.get(link["complaint_b_id"])
    return a, b


def review_queue(user: dict, include_decided: bool = False) -> list:
    """Open (pending) alerts the user's bureau is part of; super admin sees all."""
    if user["role"] == "citizen":
        return []
    rows = []
    for l in case_links.all_rows():
        if l["relationship_type"] not in REVIEWABLE:
            continue
        decided = l["review_status"] != PENDING_REVIEW
        if decided != include_decided:
            continue
        a, b = _active_pair(l)
        if not a or not b or not _involved(user, a, b):
            continue
        if not include_decided and DISPOSED in (a["status"], b["status"]):
            continue
        rows.append({"link": l, "a": a, "b": b,
                     "bureau_a": bureaus.get(a["bureau_id"])["code"],
                     "bureau_b": bureaus.get(b["bureau_id"])["code"],
                     "institution": institutions.get(a["institution_id"]),
                     "reviewer": users.get_by_id(l["reviewed_by"]) if l["reviewed_by"] else None})
    rows.sort(key=lambda r: (r["link"]["relationship_type"] != OVERLAPPING_PROCEEDING,
                             -(r["link"]["score"] or 0)))
    return rows


def can_decide(user: dict, a: dict, b: dict) -> bool:
    if not rbac.has_permission(user["role"], rbac.OVERLAP_REVIEW):
        return False
    if user["role"] == SUPER_ADMIN:
        return True
    return user["role"] == BUREAU_ADMIN and user.get("bureau_id") in (a["bureau_id"], b["bureau_id"])


def review_detail(user: dict, link_id: int) -> dict:
    l = case_links.get(link_id)
    if not l:
        raise NotFound("overlap not found")
    a, b = _active_pair(l)
    if user["role"] == "citizen" or not _involved(user, a, b):
        raise NotFound("overlap not found")
    sides = []
    for c in (a, b):
        p = access_service.present(user, c)
        p["bureau_code"] = bureaus.get(c["bureau_id"])["code"]
        p["bureau_name"] = bureaus.get(c["bureau_id"])["name"]
        p["officer_name"] = (users.get_by_id(c["officer_id"])["display_name"]
                             if p["access"] == "full" and c["officer_id"] else None)
        sides.append(p)
    ev = l["evidence"] or {}
    high, medium = thresholds(ev.get("scores", {}).get("mode", "lexical"))
    score = l["score"] or 0
    return {
        "link": l, "a": sides[0], "b": sides[1], "evidence": ev,
        "institution": institutions.get(a["institution_id"]),
        "similarity_label": "High" if score >= high else "Medium" if score >= medium else "Low",
        "different_bureaus": a["bureau_id"] != b["bureau_id"],
        "can_decide": can_decide(user, a, b) and l["review_status"] == PENDING_REVIEW
                      and l["relationship_type"] in REVIEWABLE,
        "reviewer": users.get_by_id(l["reviewed_by"]) if l["reviewed_by"] else None,
    }


_ACTION = {CONFIRMED: "overlap_confirmed", DISMISSED: "overlap_dismissed",
           RELATED_DISTINCT: "overlap_related_distinct"}
_LABEL = {CONFIRMED: "confirmed as overlapping", DISMISSED: "dismissed",
          RELATED_DISTINCT: "marked related but distinct"}


def decide(actor: dict, link_id: int, decision: str, reason: str) -> dict:
    """Human decision on an overlap. A recorded reason is mandatory."""
    if not rbac.has_permission(actor["role"], rbac.OVERLAP_REVIEW):
        raise PermissionDenied("your role cannot review overlaps")
    if decision not in REVIEW_DECISIONS:
        raise ValidationError("choose confirm, dismiss, or related but distinct")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("a reason is required to record this decision")
    l = case_links.get(link_id)
    if not l:
        raise NotFound("overlap not found")
    a, b = _active_pair(l)
    if not _involved(actor, a, b):
        raise NotFound("overlap not found")
    if not can_decide(actor, a, b):
        raise PermissionDenied("only a bureau admin of an involved bureau, or a super admin, can decide")
    if l["review_status"] != PENDING_REVIEW:
        raise ValidationError("this overlap has already been decided")
    if l["relationship_type"] not in REVIEWABLE:
        raise ValidationError("this link does not need a decision")
    now = time.time()
    with pool.transaction():
        l = case_links.update(link_id, review_status=decision, reviewed_by=actor["id"],
                              reviewed_at=now, reason=reason)
        for x, other in ((a, b), (b, a)):
            case_actions.add(x["id"], _ACTION[decision], actor_id=actor["id"],
                             bureau_id=actor.get("bureau_id") or x["bureau_id"],
                             from_value=PENDING_REVIEW, to_value=decision,
                             remarks=f"{other['case_number']} {_LABEL[decision]}. Reason: {reason}",
                             at=now)
            complaints.update(x["id"], last_action_at=now,
                              last_action=_ACTION[decision].replace("_", " ").capitalize())
        audit.add(actor["username"], f"overlap.{decision}", target_type="case_link",
                  target_id=link_id, detail={"cases": [a["case_number"], b["case_number"]],
                                             "reason": reason})
        recipients = {}
        for x in (a, b):
            for u in users.list_all(bureau_id=x["bureau_id"]):
                if u["is_active"] and u["role"] == BUREAU_ADMIN:
                    recipients[u["id"]] = u
            if x["officer_id"]:
                recipients[x["officer_id"]] = users.get_by_id(x["officer_id"])
        recipients.pop(actor["id"], None)
        for u in recipients.values():
            notifications.add(u["id"], "overlap_decision",
                              f"{a['case_number']} and {b['case_number']} {_LABEL[decision]}.",
                              complaint_id=a["id"])
    return l


def pending_alert_count() -> int:
    """Open overlapping-proceeding alerts across the system (dashboard figure)."""
    n = 0
    for l in case_links.all_rows():
        if l["relationship_type"] == OVERLAPPING_PROCEEDING and l["review_status"] == PENDING_REVIEW:
            a, b = _active_pair(l)
            if a and b and DISPOSED not in (a["status"], b["status"]):
                n += 1
    return n

"""SLA states and the (idempotent) deadline notification sweep."""
from __future__ import annotations

import time

from db import complaints, notifications, users
from domain.constants import (ACTIVE_STATUSES, BUREAU_ADMIN, DEFAULT_SLA_DAYS, DISPOSED,
                              DUE_SOON, DUE_SOON_FRACTION, ON_TRACK, OVERDUE, SLA_DAYS,
                              SUPER_ADMIN)

DAY = 86400.0


def disposal_deadline(category: str, received_at: float) -> float:
    from domain.constants import QUERY_CATEGORY, QUERY_SLA_DAYS
    days = QUERY_SLA_DAYS if category == QUERY_CATEGORY else SLA_DAYS.get(category, DEFAULT_SLA_DAYS)
    return received_at + days * DAY


def state(c: dict, now: float | None = None):
    """on_track / due_soon / overdue, or None for a disposed case (no clock running)."""
    if c["status"] == DISPOSED or not c.get("expected_disposal_at") or not c.get("received_at"):
        return None
    now = time.time() if now is None else now
    due = c["expected_disposal_at"]
    if now > due:
        return OVERDUE
    window = due - c["received_at"]
    if window > 0 and (due - now) <= window * DUE_SOON_FRACTION:
        return DUE_SOON
    return ON_TRACK


def _recipients(c: dict) -> set:
    ids = set()
    if c.get("officer_id"):
        ids.add(c["officer_id"])
    for u in users.list_all(bureau_id=c["bureau_id"]):
        if u["role"] == BUREAU_ADMIN and u["is_active"]:
            ids.add(u["id"])
    return ids


def run_sweep(now: float | None = None) -> int:
    """Create due-soon / overdue notifications, one per case per type per
    recipient. Safe to call as often as you like (CLI + dashboard load)."""
    now = time.time() if now is None else now
    made = 0
    for c in complaints.all_rows():
        if c["status"] not in ACTIVE_STATUSES:
            continue
        st = state(c, now)
        if st not in (DUE_SOON, OVERDUE):
            continue
        label = "overdue" if st == OVERDUE else "due soon"
        msg = f"{c['case_number']} is {label} (category: {c['category']})."
        for rid in _recipients(c):
            if not notifications.exists(rid, c["id"], st):
                notifications.add(rid, st, msg, complaint_id=c["id"])
                made += 1
    return made


def summary(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    counts = {ON_TRACK: 0, DUE_SOON: 0, OVERDUE: 0}
    for c in complaints.all_rows():
        st = state(c, now)
        if st:
            counts[st] += 1
    return counts

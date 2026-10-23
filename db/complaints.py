"""Complaint persistence and case-number generation."""
from db import pool
from db.table import Table
from domain.constants import CODE_PAD, CODE_PREFIX

COLS = ("id", "case_number", "institution_id", "institution_name_stated", "citizen_id", "channel", "title",
        "description", "category", "priority", "status", "bureau_id", "officer_id",
        "received_at", "expected_disposal_at", "last_action_at", "last_action",
        "next_action", "text_version", "created_by", "created_at", "updated_at")

T = Table("complaints", COLS, defaults={"status": "Pending", "priority": "medium",
                                        "text_version": 1, "channel": "official"})
_REQUIRED = ("title", "description", "category", "bureau_id")


def next_case_number() -> str:
    return f"{CODE_PREFIX}{pool.next_seq('case_number'):0{CODE_PAD}d}"


def insert(**f) -> dict:
    missing = [k for k in _REQUIRED if f.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing required complaint fields: {missing}")
    f.setdefault("case_number", next_case_number())
    return T.insert(**f)


def get(cid):
    return T.get(cid)


def get_by_number(case_number):
    return T.get_by("case_number", case_number)


def update(cid, **fields):
    return T.update(cid, **fields)


def all_rows():
    return T.all()


def for_institution(institution_id):
    return [c for c in T.all() if c["institution_id"] == institution_id]

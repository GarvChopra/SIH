"""Links between complaints. Stored once per unordered pair (a < b)."""
from db.table import Table

COLS = ("id", "complaint_a_id", "complaint_b_id", "relationship_type", "score",
        "evidence", "review_status", "reviewed_by", "reviewed_at", "reason",
        "text_signature", "created_at")
T = Table("case_links", COLS, json_cols=("evidence",),
          defaults={"review_status": "pending"})


def order(a, b):
    return (a, b) if a < b else (b, a)


def get_pair(a, b):
    a, b = order(a, b)
    return next((l for l in T.all()
                 if l["complaint_a_id"] == a and l["complaint_b_id"] == b), None)


def get(lid):
    return T.get(lid)


def upsert(a, b, **fields):
    a, b = order(a, b)
    existing = get_pair(a, b)
    if existing:
        return T.update(existing["id"], **fields)
    return T.insert(complaint_a_id=a, complaint_b_id=b, **fields)


def update(lid, **fields):
    return T.update(lid, **fields)


def for_complaint(cid):
    return [l for l in T.all() if cid in (l["complaint_a_id"], l["complaint_b_id"])]


def other_side(link, cid):
    return link["complaint_b_id"] if link["complaint_a_id"] == cid else link["complaint_a_id"]


def all_rows():
    return T.all()

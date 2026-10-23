"""Append-only case history.

There is deliberately no update() or delete() here. In PostgreSQL a trigger
(see schema.py) also rejects UPDATE/DELETE, so the guarantee does not depend on
this module being the only writer.
"""
import time

from db.table import Table

T = Table("case_actions", ("id", "complaint_id", "action_type", "actor_id", "bureau_id",
                           "at", "from_value", "to_value", "remarks", "document_ids"),
          json_cols=("document_ids",), append_only=True)


def add(complaint_id, action_type, *, actor_id=None, bureau_id=None, from_value=None,
        to_value=None, remarks=None, document_ids=None, at=None):
    return T.insert(complaint_id=complaint_id, action_type=action_type, actor_id=actor_id,
                    bureau_id=bureau_id, at=at if at is not None else time.time(),
                    from_value=from_value, to_value=to_value, remarks=remarks,
                    document_ids=document_ids or [])


def for_complaint(complaint_id):
    return sorted((a for a in T.all() if a["complaint_id"] == complaint_id),
                  key=lambda a: (a["at"], a["id"]))


def all_rows():
    return T.all()

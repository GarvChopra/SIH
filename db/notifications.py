"""In-app notification inbox."""
import time

from db.table import Table

T = Table("notifications", ("id", "recipient_id", "complaint_id", "type", "message",
                            "read_at", "created_at"))


def add(recipient_id, type_, message, complaint_id=None):
    return T.insert(recipient_id=recipient_id, complaint_id=complaint_id, type=type_,
                    message=message)


def exists(recipient_id, complaint_id, type_):
    return any(n["recipient_id"] == recipient_id and n["complaint_id"] == complaint_id
               and n["type"] == type_ for n in T.all())


def for_user(user_id, unread_only=False, limit=200):
    rows = [n for n in T.all() if n["recipient_id"] == user_id
            and (not unread_only or n["read_at"] is None)]
    rows.sort(key=lambda n: (n["created_at"], n["id"]), reverse=True)
    return rows[:limit]


def unread_count(user_id):
    return sum(1 for n in T.all() if n["recipient_id"] == user_id and n["read_at"] is None)


def mark_read(user_id, nid=None):
    now = time.time()
    for n in list(T.all()):
        if n["recipient_id"] == user_id and n["read_at"] is None and nid in (None, n["id"]):
            T.update(n["id"], read_at=now)

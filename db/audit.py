"""System-wide audit log for admin actions not scoped to one grievance."""
import json
import time

from db import pool

_COLS = ("id", "actor", "action", "target_type", "target_id", "detail", "created_at")


def _mem():
    return pool.STATE["mem"]["audit_log"]


def add(actor, action, *, target_type=None, target_id=None, detail=None):
    now = time.time()
    tid = str(target_id) if target_id is not None else None
    if pool.is_memory():
        row = {"id": pool.next_seq("audit"), "actor": actor, "action": action,
               "target_type": target_type, "target_id": tid,
               "detail": detail or {}, "created_at": now}
        _mem().append(row)
        return dict(row)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO audit_log (actor, action, target_type, target_id, detail, created_at)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
            (actor, action, target_type, tid, json.dumps(detail or {}), now),
        )
        rid = cur.fetchone()[0]
        pool.commit(conn)
    return {"id": rid, "actor": actor, "action": action, "target_type": target_type,
            "target_id": tid, "detail": detail or {}, "created_at": now}


def list_recent(limit=200):
    if pool.is_memory():
        rows = [dict(e) for e in _mem()]
    else:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(_COLS)} FROM audit_log ORDER BY created_at DESC LIMIT %s",
                        (limit,))
            rows = [dict(zip(_COLS, r)) for r in cur.fetchall()]
    return sorted(rows, key=lambda e: (e["created_at"], e["id"]), reverse=True)[:limit]

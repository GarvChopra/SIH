"""One small table abstraction so every table has BOTH persistence paths.

`Table` is the single place that knows about memory-vs-PostgreSQL. A db module
declares its columns once and gets insert/get/update/all/delete for both
backends. Filtering is done in Python over `all()` (cached per request): the
data set is institution-complaint scale, not event-stream scale.
"""
from __future__ import annotations

import time

from db import pool

try:
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover - postgres driver not installed
    Jsonb = None


def _bust(name: str) -> None:
    try:
        from flask import g, has_request_context
        if has_request_context():
            g.pop(f"_tbl_{name}", None)
    except Exception:  # noqa: BLE001
        pass


class Table:
    def __init__(self, name, cols, *, json_cols=(), defaults=None,
                 append_only=False, ts_create="created_at", ts_update="updated_at"):
        self.name = name
        self.cols = tuple(cols)
        self.json_cols = frozenset(json_cols)
        self.defaults = dict(defaults or {})
        self.append_only = append_only
        self.ts_create = ts_create if ts_create in self.cols else None
        self.ts_update = ts_update if ts_update in self.cols else None
        self._sel = ", ".join(self.cols)

    # -- helpers ------------------------------------------------------------
    def _mem(self):
        return pool.STATE["mem"].setdefault(self.name, [])

    def _pg_value(self, col, v):
        if col in self.json_cols and v is not None:
            return Jsonb(v)
        return v

    def _row(self, values):
        return dict(zip(self.cols, values))

    # -- reads --------------------------------------------------------------
    def _fetch_all(self):
        if pool.is_memory():
            return [dict(r) for r in self._mem()]
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {self._sel} FROM {self.name} ORDER BY id")
            return [self._row(r) for r in cur.fetchall()]

    def all(self):
        """Every row, cached for the life of a request. Callers must not mutate."""
        try:
            from flask import g, has_request_context
            if has_request_context():
                key = f"_tbl_{self.name}"
                rows = g.get(key)
                if rows is None:
                    rows = self._fetch_all()
                    g.setdefault(key, rows)
                return rows
        except Exception:  # noqa: BLE001
            pass
        return self._fetch_all()

    def get(self, id_):
        if id_ is None:
            return None
        if pool.is_memory():
            return next((dict(r) for r in self._mem() if r["id"] == id_), None)
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {self._sel} FROM {self.name} WHERE id=%s", (id_,))
            r = cur.fetchone()
            return self._row(r) if r else None

    def get_by(self, col, value):
        if pool.is_memory():
            return next((dict(r) for r in self._mem() if r.get(col) == value), None)
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {self._sel} FROM {self.name} WHERE {col}=%s LIMIT 1", (value,))
            r = cur.fetchone()
            return self._row(r) if r else None

    def where(self, **eq):
        return [r for r in self.all() if all(r.get(k) == v for k, v in eq.items())]

    # -- writes -------------------------------------------------------------
    def insert(self, **f) -> dict:
        row = {c: None for c in self.cols}
        row.update(self.defaults)
        row.update({k: v for k, v in f.items() if k in self.cols and k != "id"})
        now = time.time()
        if self.ts_create and row.get(self.ts_create) is None:
            row[self.ts_create] = now
        if self.ts_update and row.get(self.ts_update) is None:
            row[self.ts_update] = now
        if pool.is_memory():
            row["id"] = pool.next_seq(self.name)
            self._mem().append(dict(row))
            _bust(self.name)
            return row
        cols = [c for c in self.cols if c != "id"]
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self.name} ({', '.join(cols)}) "
                f"VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
                [self._pg_value(c, row[c]) for c in cols])
            row["id"] = cur.fetchone()[0]
            pool.commit(conn)
        _bust(self.name)
        return row

    def update(self, id_, **fields) -> dict:
        if self.append_only:
            raise PermissionError(f"{self.name} is append-only")
        patch = {k: v for k, v in fields.items() if k in self.cols and k != "id"}
        if self.ts_update:
            patch[self.ts_update] = time.time()
        if pool.is_memory():
            for r in self._mem():
                if r["id"] == id_:
                    r.update(patch)
                    _bust(self.name)
                    return dict(r)
            raise KeyError(id_)
        sets = ", ".join(f"{k}=%s" for k in patch)
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {self.name} SET {sets} WHERE id=%s",
                        [*[self._pg_value(k, v) for k, v in patch.items()], id_])
            pool.commit(conn)
        _bust(self.name)
        return self.get(id_)

    def delete(self, id_) -> None:
        if self.append_only:
            raise PermissionError(f"{self.name} is append-only")
        if pool.is_memory():
            mem = self._mem()
            mem[:] = [r for r in mem if r["id"] != id_]
            _bust(self.name)
            return
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {self.name} WHERE id=%s", (id_,))
            pool.commit(conn)
        _bust(self.name)

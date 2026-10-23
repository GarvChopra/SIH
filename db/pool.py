"""Persistence backend selection.

Order of preference
------------------
1. **PostgreSQL** — the primary production datastore. Required in production.
2. **Firestore** — persistent fallback used only when Postgres is unavailable.
   (Credential/config wiring is completed in a later phase; the hook is here.)
3. **In-memory** — development / test convenience ONLY. Never used in
   production: if production cannot reach Postgres or Firestore, `init_db()`
   raises and the process exits loudly instead of pretending to work.

Firestore mode reuses the exact in-memory code path in every db/*.py module
(they all branch on `pool.is_memory()`), loading collections into STATE["mem"]
at boot and writing back via `flush_to_firestore()`.
"""
import base64
import json
import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar

from config import Config

log = logging.getLogger("samanvay.db")

_PG_OK = False
try:
    import psycopg
    _PG_OK = True
except ImportError:
    psycopg = None

# Short-lived connections, opened per db call. We deliberately do NOT use
# psycopg_pool: its background connection-opener threads proved unreliable under
# gunicorn on the deploy host (Render) — boot connected fine, then every request
# timed out in ConnectionPool.getconn(). The pooling that matters is done
# server-side by Supabase's Supavisor (the pooler host in DATABASE_URL).
# prepare_threshold=None is required when DATABASE_URL points at the transaction
# pooler (port 6543) and harmless on the session pooler (5432) / a direct DSN.
_CONNECT_KWARGS = dict(
    connect_timeout=10, prepare_threshold=None,
    keepalives=1, keepalives_idle=30, keepalives_interval=5, keepalives_count=3,
)

_FS_OK = False
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    _FS_OK = True
except ImportError:
    firebase_admin = None
    firestore = None

_MEM_TABLES = (
    "users", "bureaus", "institutions", "institution_aliases", "complaints",
    "case_actions", "case_links", "complaint_embeddings", "notifications",
    "documents", "audit_log",
)

# table name -> the next_seq() name each db module uses for "id"
_ID_SEQ_NAME = {t: t for t in _MEM_TABLES}

STATE = {"mode": "memory", "mem": {}, "seq": {}, "firestore_db": None}


def reset_memory_store() -> None:
    STATE["mode"] = "memory"
    STATE["mem"] = {t: [] for t in _MEM_TABLES}
    STATE["seq"] = {}
    STATE["firestore_db"] = None


def is_memory() -> bool:
    # Firestore mode is a durable variant of memory mode — same STATE["mem"] shape,
    # so every db/*.py module's existing memory branch works unchanged.
    return STATE["mode"] in ("memory", "firestore")


def is_firestore() -> bool:
    return STATE["mode"] == "firestore"


_TX: ContextVar = ContextVar("samanvay_tx", default=None)


@contextmanager
def connection():
    """A short-lived psycopg connection, closed when the caller is done.

    Inside `transaction()` every caller shares the transaction's connection, and
    `commit()` becomes a no-op until the transaction block exits.
    """
    if STATE["mode"] != "postgres":
        raise RuntimeError("pool.connection() called outside postgres mode")
    shared = _TX.get()
    if shared is not None:
        yield shared
        return
    conn = psycopg.connect(Config.DATABASE_URL, **_CONNECT_KWARGS)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def commit(conn) -> None:
    """Commit unless a surrounding `transaction()` owns the commit."""
    if _TX.get() is None:
        conn.commit()


@contextmanager
def transaction():
    """Make several db calls atomic.

    PostgreSQL: one connection, one COMMIT at the end, ROLLBACK on any exception.
    In-memory/Firestore mode there is nothing to roll back, so callers validate
    before they mutate; this is then a plain pass-through.
    """
    if STATE["mode"] != "postgres" or _TX.get() is not None:
        yield
        return
    conn = psycopg.connect(Config.DATABASE_URL, **_CONNECT_KWARGS)
    token = _TX.set(conn)
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        _TX.reset(token)
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def next_seq(name: str) -> int:
    if STATE["mode"] == "postgres":
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE SEQUENCE IF NOT EXISTS seq_%s" % name)
                cur.execute("SELECT nextval(%s)", (f"seq_{name}",))
                val = cur.fetchone()[0]
            conn.commit()
            return int(val)
    STATE["seq"][name] = STATE["seq"].get(name, 0) + 1
    return STATE["seq"][name]


# ---------------------------------------------------------------- Firestore --

def _load_credentials_dict():
    """Accept the service-account credentials in any of the common forms:
    raw JSON in an env var, a base64 blob, or a path to a JSON file
    (GOOGLE_APPLICATION_CREDENTIALS / FIREBASE_SERVICE_ACCOUNT_JSON)."""
    for var in ("FIREBASE_KEY_JSON", "FIREBASE_SERVICE_ACCOUNT_JSON"):
        raw_json = os.environ.get(var, "").strip()
        if raw_json.startswith("{"):
            return json.loads(raw_json)

    b64 = "".join(os.environ.get("FIREBASE_CREDENTIALS_B64", "").split())
    if b64:
        return json.loads(base64.b64decode(b64))

    for var in ("GOOGLE_APPLICATION_CREDENTIALS", "FIREBASE_SERVICE_ACCOUNT_JSON"):
        path = os.environ.get(var, "").strip()
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)

    return None


def _init_firestore():
    """Returns a firestore client, or None if not configured / not installed."""
    if not _FS_OK:
        return None
    try:
        cred_dict = _load_credentials_dict()
        if not cred_dict:
            return None
        if not firebase_admin._apps:
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
        return firestore.client()
    except Exception as e:  # noqa: BLE001
        log.error("Firestore init failed: %s: %s", type(e).__name__, e)
        return None


def _firestore_load(db) -> None:
    """Pull every collection from Firestore into STATE['mem']."""
    for table in _MEM_TABLES:
        try:
            docs = db.collection(table).stream()
            STATE["mem"][table] = [d.to_dict() for d in docs]
        except Exception as e:  # noqa: BLE001
            log.error("Firestore load '%s' failed: %s: %s", table, type(e).__name__, e)
            STATE["mem"][table] = []


def _restore_sequences() -> None:
    """After loading existing data, make sure next_seq() won't hand out an id
    that already exists in the loaded rows."""
    for table, seq_name in _ID_SEQ_NAME.items():
        ids = [r.get("id") for r in STATE["mem"].get(table, []) if isinstance(r.get("id"), int)]
        if ids:
            STATE["seq"][seq_name] = max(STATE["seq"].get(seq_name, 0), max(ids))

    # complaint case numbers (AICTE-CMP-00123) are driven by a separate sequence
    nums = []
    for row in STATE["mem"].get("complaints", []):
        digits = "".join(ch for ch in str(row.get("case_number", "")) if ch.isdigit())
        if digits:
            nums.append(int(digits))
    if nums:
        STATE["seq"]["case_number"] = max(STATE["seq"].get("case_number", 0), max(nums))


def flush_to_firestore() -> None:
    """Write the current in-memory snapshot back to Firestore. Call this after
    seeding, and ideally after requests that write data (see app.py note)."""
    db = STATE.get("firestore_db")
    if not db:
        return
    for table in _MEM_TABLES:
        col = db.collection(table)
        for row in STATE["mem"].get(table, []):
            doc_id = str(row.get("id"))
            if doc_id and doc_id != "None":
                col.document(doc_id).set(row, merge=False)


# --------------------------------------------------------------------- init --

def _try_postgres() -> bool:
    """Attempt to connect + ensure schema. Returns True on success."""
    dsn = Config.DATABASE_URL
    if not (dsn and _PG_OK):
        return False
    from db import schema

    for attempt in range(1, 4):
        try:
            with psycopg.connect(dsn, **_CONNECT_KWARGS) as conn:
                schema.ensure(conn)
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            STATE["mode"] = "postgres"
            log.info("PostgreSQL connected (attempt %d)", attempt)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("PostgreSQL attempt %d/3 failed: %s: %s",
                        attempt, type(e).__name__, e)
            if attempt < 3:
                time.sleep(3)
    return False


def _try_firestore() -> bool:
    """Attempt the persistent Firestore fallback. Returns True on success.

    Only used in production (or when explicitly opted in with
    ALLOW_FIRESTORE_IN_DEV=1). This stops a local dev run that happens to have
    service-account credentials in its environment from silently reading and
    writing the shared cloud database."""
    if not Config.is_production() and os.environ.get("ALLOW_FIRESTORE_IN_DEV") != "1":
        return False
    fs_db = _init_firestore()
    if not fs_db:
        return False
    STATE["firestore_db"] = fs_db
    STATE["mode"] = "firestore"
    _firestore_load(fs_db)
    _restore_sequences()
    log.warning("Entering Firestore FALLBACK mode — PostgreSQL is unavailable")
    return True


def init_db() -> None:
    reset_memory_store()

    # 1) PostgreSQL — the primary production datastore.
    if _try_postgres():
        return

    # 2) Firestore — persistent fallback when Postgres is down.
    if _try_firestore():
        return

    # 3) Neither backend is available.
    if Config.is_production():
        raise RuntimeError(
            "SAMANVAY cannot start: PostgreSQL is unavailable and no Firestore "
            "fallback is configured. In-memory storage is NEVER used in "
            "production because it silently loses data. Fix DATABASE_URL / "
            "database connectivity (or configure Firestore) and redeploy."
        )

    STATE["mode"] = "memory"
    log.warning("No database reachable — using IN-MEMORY store "
                "(development only; data is lost on restart)")

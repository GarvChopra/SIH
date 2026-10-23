"""Runs the PostgreSQL code path against a REAL database.

Skipped unless TEST_DATABASE_URL is set (the normal suite is offline). Example:

    set TEST_DATABASE_URL=postgresql://user@localhost:5544/samanvay_test
    pytest tests/test_postgres.py

WARNING: every table is emptied before each test. Point it at a throwaway database.
"""
import os

import pytest

DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

TABLES = ("notifications, documents, complaint_embeddings, case_links, institution_aliases, "
          "complaints, institutions, users, bureaus, audit_log")


@pytest.fixture()
def pg(monkeypatch):
    import psycopg
    from config import Config
    from db import pool, schema, seeds
    monkeypatch.setattr(Config, "DATABASE_URL", DSN)
    with psycopg.connect(DSN) as conn:
        schema.ensure(conn)
        schema.ensure(conn)                                   # idempotent
        with conn.cursor() as cur:
            # case_actions is append-only by trigger: disable it only to empty the test database
            cur.execute("ALTER TABLE case_actions DISABLE TRIGGER trg_case_actions_immutable")
            cur.execute(f"TRUNCATE case_actions, {TABLES} RESTART IDENTITY CASCADE")
            cur.execute("ALTER TABLE case_actions ENABLE TRIGGER trg_case_actions_immutable")
            cur.execute("DROP SEQUENCE IF EXISTS seq_case_number")
        conn.commit()
    pool.reset_memory_store()
    pool.STATE["mode"] = "postgres"
    seeds.seed_essential()
    seeds.seed_demo_accounts()
    seeds.seed_demo_citizen()
    yield pool
    pool.reset_memory_store()


def actor(username):
    from db import users
    u = users.get_by_username(username)
    return {k: u[k] for k in ("id", "username", "display_name", "role", "bureau_id", "region")} | {
        "department": u["department"]}


def make_inst():
    from db import institutions
    return institutions.create("123456", "ABC Engineering College", "Greater Noida", "North")


def test_schema_roundtrip_json_columns_and_unique_keys(pg):
    from db import institutions, case_links, complaints
    inst = institutions.create("777", "Json College", metadata={"a": [1, 2, {"b": 3}]})
    assert institutions.get(inst["id"])["metadata"] == {"a": [1, 2, {"b": 3}]}
    with pytest.raises(ValueError):
        institutions.create("777", "Dup", None)
    assert pg.STATE["mode"] == "postgres"
    assert case_links.all_rows() == [] and complaints.all_rows() == []


def test_lifecycle_and_atomic_history_on_postgres(pg):
    from db import case_actions, complaints
    from services import complaint_service as cs
    make_inst()
    c = cs.create(actor("pgrc.officer"), aicte_id="123456", title="Unauthorized examination fees",
                  description="Students are being charged unauthorized examination fees.",
                  category="Unauthorized fees")
    assert c["case_number"] == "AICTE-CMP-00001"
    cs.assign(actor("pgrc.admin"), c["id"], actor("pgrc.officer")["id"])
    cs.change_status(actor("pgrc.officer"), c["id"], "Under Process")
    kinds = [a["action_type"] for a in case_actions.for_complaint(c["id"])]
    assert kinds == ["registered", "assigned", "status_changed"]
    assert complaints.get(c["id"])["status"] == "Under Process"


def test_transaction_rolls_back_status_change_if_history_write_fails(pg, monkeypatch):
    from db import case_actions, complaints
    from services import complaint_service as cs
    make_inst()
    c = cs.create(actor("pgrc.officer"), aicte_id="123456", title="Fee matter at the college",
                  description="Students are charged extra fees here.", category="Unauthorized fees")
    cs.assign(actor("pgrc.admin"), c["id"], actor("pgrc.officer")["id"])

    def boom(*a, **k):
        raise RuntimeError("disk full")
    with monkeypatch.context() as m:
        m.setattr(case_actions, "add", boom)
        with pytest.raises(RuntimeError):
            cs.change_status(actor("pgrc.officer"), c["id"], "Under Process")
    # the status UPDATE happened in the same transaction, so it was rolled back too
    assert complaints.get(c["id"])["status"] == "Pending"


def test_database_trigger_makes_history_append_only(pg):
    import psycopg
    from db import case_actions
    from services import complaint_service as cs
    make_inst()
    c = cs.create(actor("pgrc.officer"), aicte_id="123456", title="Trigger test case title",
                  description="Some description of the trigger test case.", category="Harassment")
    row = case_actions.for_complaint(c["id"])[0]
    with psycopg.connect(DSN) as conn:
        for sql in (f"UPDATE case_actions SET remarks='tampered' WHERE id={row['id']}",
                    f"DELETE FROM case_actions WHERE id={row['id']}"):
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                conn.cursor().execute(sql)
            conn.rollback()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):   # no cascade delete from complaints
            conn.cursor().execute(f"DELETE FROM complaints WHERE id={c['id']}")
        conn.rollback()


def test_check_constraints_reject_bad_values(pg):
    import psycopg
    with psycopg.connect(DSN) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.cursor().execute(
                "INSERT INTO users (username, display_name, role, pin_hash) VALUES ('x','x','reporter','h')")
        conn.rollback()


def test_overlap_detection_and_review_on_postgres(pg):
    from db import case_actions, case_links, complaints
    from services import complaint_service as cs, overlap_service as ov
    make_inst()
    a = cs.create(actor("pgrc.officer"), aicte_id="123456", title="Unauthorized examination fees",
                  description="Students are being charged unauthorized examination fees.",
                  category="Unauthorized fees")
    b = cs.create(actor("rb.officer"), aicte_id="123456", title="Excess exam charges",
                  description="The institution is collecting excess examination charges.",
                  category="Unauthorized fees")
    link = case_links.get_pair(a["id"], b["id"])
    assert link["relationship_type"] == "overlapping_proceeding"
    assert link["evidence"]["bureaus"] == ["PGRC", "RB"]               # JSONB round-trip
    ov.decide(actor("rb.admin"), link["id"], "confirmed", "Same fee demand across both bureaus")
    assert case_links.get(link["id"])["review_status"] == "confirmed"
    for c in (a, b):
        assert case_actions.for_complaint(c["id"])[-1]["action_type"] == "overlap_confirmed"


def test_citizen_flow_and_dashboard_on_postgres(pg):
    from services import citizen_service as ctz, complaint_service as cs, dashboard_service
    make_inst()
    cit = actor("citizen.demo")
    c = cs.create_for_citizen(cit, aicte_id="123456", category="Harassment",
                              title="Ragging in the hostel",
                              description="First year students are being ragged by seniors in the hostel.")
    assert ctz.list_own(cit)[0]["case_number"] == c["case_number"]
    d = dashboard_service.build(actor("superadmin"))
    assert d["total"] == 1 and d["by_status"]["Pending"] == 1


def test_full_seed_and_web_app_on_postgres(pg):
    from db import complaints
    from scripts import seed_aicte_demo as seed
    result = seed.build()
    assert result["institutions"] == 30 and result["complaints"] > 140
    assert seed.build() == {"skipped": "institutions already exist"}
    from app import create_app
    app = create_app()
    from tests.conftest import login
    cl = app.test_client()
    login(cl, "superadmin")
    for p in ("/", "/cases", "/overlaps", "/institutions/123456", "/reports/bureau-wise"):
        assert cl.get(p).status_code == 200, p
    assert len(complaints.all_rows()) == result["complaints"]

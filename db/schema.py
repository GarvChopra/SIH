"""PostgreSQL DDL. Idempotent. Memory mode does not use this.

This is the SAMANVAY (AICTE) schema. It replaces the old campus-grievance
schema outright: point it at a fresh database. `CREATE TABLE IF NOT EXISTS`
will NOT migrate an old UNIFIX `users` table (its role CHECK is different).

Timestamps are epoch floats (DOUBLE PRECISION), matching the in-memory path.
"""

DDL = """
CREATE TABLE IF NOT EXISTS bureaus (
    id      BIGSERIAL PRIMARY KEY,
    code    TEXT UNIQUE NOT NULL,
    name    TEXT NOT NULL,
    region  TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN
                    ('super_admin','bureau_admin','officer','regional_user','citizen')),
    pin_hash      TEXT NOT NULL,
    department    TEXT,
    contact       TEXT,
    bureau_id     BIGINT REFERENCES bureaus(id),
    region        TEXT,
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    DOUBLE PRECISION,
    created_by    TEXT,
    must_change_pin BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_bureau   ON users(bureau_id);

CREATE TABLE IF NOT EXISTS institutions (
    id                   BIGSERIAL PRIMARY KEY,
    aicte_permanent_id   TEXT UNIQUE NOT NULL,
    name                 TEXT NOT NULL,
    address              TEXT,
    region               TEXT,
    metadata             JSONB,
    created_at           DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS institution_aliases (
    id              BIGSERIAL PRIMARY KEY,
    institution_id  BIGINT NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    alias           TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('suggested','approved')),
    complaint_id    BIGINT,
    created_at      DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_alias_inst ON institution_aliases(institution_id);

CREATE TABLE IF NOT EXISTS complaints (
    id                      BIGSERIAL PRIMARY KEY,
    case_number             TEXT UNIQUE NOT NULL,
    institution_id          BIGINT REFERENCES institutions(id),
    institution_name_stated TEXT,
    citizen_id              BIGINT REFERENCES users(id),
    channel                 TEXT DEFAULT 'official',
    title                   TEXT NOT NULL,
    description             TEXT NOT NULL,
    category                TEXT NOT NULL,
    priority                TEXT NOT NULL DEFAULT 'medium'
                            CHECK (priority IN ('low','medium','high','critical')),
    status                  TEXT NOT NULL DEFAULT 'Pending'
                            CHECK (status IN ('Pending','Under Process','Transferred',
                                              'Escalated','Disposed')),
    bureau_id               BIGINT NOT NULL REFERENCES bureaus(id),
    officer_id              BIGINT REFERENCES users(id),
    received_at             DOUBLE PRECISION,
    expected_disposal_at    DOUBLE PRECISION,
    last_action_at          DOUBLE PRECISION,
    last_action             TEXT,
    next_action             TEXT,
    text_version            INTEGER DEFAULT 1,
    created_by              BIGINT,
    created_at              DOUBLE PRECISION,
    updated_at              DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_cmp_inst   ON complaints(institution_id);
CREATE INDEX IF NOT EXISTS idx_cmp_bureau ON complaints(bureau_id);
CREATE INDEX IF NOT EXISTS idx_cmp_status ON complaints(status);

-- Append-only history. No ON DELETE CASCADE: a complaint with history can never
-- be deleted out from under its audit trail.
CREATE TABLE IF NOT EXISTS case_actions (
    id            BIGSERIAL PRIMARY KEY,
    complaint_id  BIGINT NOT NULL REFERENCES complaints(id),
    action_type   TEXT NOT NULL,
    actor_id      BIGINT,
    bureau_id     BIGINT,
    at            DOUBLE PRECISION NOT NULL,
    from_value    TEXT,
    to_value      TEXT,
    remarks       TEXT,
    document_ids  JSONB
);
CREATE INDEX IF NOT EXISTS idx_actions_cmp ON case_actions(complaint_id, at);

CREATE OR REPLACE FUNCTION case_actions_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'case_actions is append-only (% rejected)', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_case_actions_immutable ON case_actions;
CREATE TRIGGER trg_case_actions_immutable
    BEFORE UPDATE OR DELETE ON case_actions
    FOR EACH ROW EXECUTE FUNCTION case_actions_immutable();

CREATE TABLE IF NOT EXISTS case_links (
    id                 BIGSERIAL PRIMARY KEY,
    complaint_a_id     BIGINT NOT NULL REFERENCES complaints(id),
    complaint_b_id     BIGINT NOT NULL REFERENCES complaints(id),
    relationship_type  TEXT NOT NULL CHECK (relationship_type IN
                         ('same_institution','related','possible_duplicate',
                          'overlapping_proceeding')),
    score              DOUBLE PRECISION,
    evidence           JSONB,
    review_status      TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN
                         ('pending','confirmed','dismissed','related_distinct')),
    reviewed_by        BIGINT,
    reviewed_at        DOUBLE PRECISION,
    reason             TEXT,
    text_signature     TEXT,
    created_at         DOUBLE PRECISION,
    CHECK (complaint_a_id < complaint_b_id),
    UNIQUE (complaint_a_id, complaint_b_id)
);

CREATE TABLE IF NOT EXISTS complaint_embeddings (
    id            BIGSERIAL PRIMARY KEY,
    complaint_id  BIGINT NOT NULL REFERENCES complaints(id),
    model         TEXT NOT NULL,
    dimension     INTEGER NOT NULL,
    vector        JSONB NOT NULL,
    text_version  INTEGER,
    created_at    DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_emb_cmp ON complaint_embeddings(complaint_id);

CREATE TABLE IF NOT EXISTS notifications (
    id            BIGSERIAL PRIMARY KEY,
    recipient_id  BIGINT NOT NULL REFERENCES users(id),
    complaint_id  BIGINT REFERENCES complaints(id),
    type          TEXT NOT NULL,
    message       TEXT NOT NULL,
    read_at       DOUBLE PRECISION,
    created_at    DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_notif_recipient ON notifications(recipient_id, read_at);

CREATE TABLE IF NOT EXISTS documents (
    id            BIGSERIAL PRIMARY KEY,
    complaint_id  BIGINT NOT NULL REFERENCES complaints(id),
    filename      TEXT NOT NULL,
    stored_name   TEXT NOT NULL,
    bureau_id     BIGINT,
    uploaded_by   BIGINT,
    uploaded_at   DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    actor       TEXT,
    action      TEXT,
    target_type TEXT,
    target_id   TEXT,
    detail      JSONB,
    created_at  DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);
"""


def ensure(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()

# SAMANVAY: AICTE Integrated Complaint Monitoring & Cross-Bureau Coordination

Prototype for the problem statement *"Integrated Dashboard for Monitoring and Tracking
Institution-Related Complaint Cases Across AICTE Bureaus/Cells"*.

One platform where bureaus see every complaint against an institution, an AI-assisted check flags
cases that may be duplicate investigations, and officials (not the AI) decide what to do about it.
Citizens file and track complaints through a separate portal backed by the same system.

All data is synthetic. Run instructions, accounts and the step-by-step judge demonstration are in
**[DEMO_GUIDE.md](DEMO_GUIDE.md)**.

## Quick start

```powershell
cd aicte-dashboard
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py          # http://127.0.0.1:5000, no database or keys needed
.venv\Scripts\python -m pytest       # 105 tests (+8 PostgreSQL tests when TEST_DATABASE_URL is set)
```

## Architecture

```
domain/        constants, roles and permissions (stdlib only)
db/            one Table helper => every table has an in-memory path AND a PostgreSQL path
services/      all rules: lifecycle, access/visibility, overlap, SLA, reports, citizen
blueprints/    portal (officials), citizen (web), api (mobile JSON), auth
templates/ static/   server-rendered pages, one stylesheet, no JS framework
scripts/       demo dataset, overlap evaluation, SLA sweep
```

Built on the UniPulse/UNIFIX Flask app: the layering, in-memory/PostgreSQL duality, auth and test
harness were kept; the campus domain (locations, recurring groups, pulse, PWA, faculty portal) was
replaced. The original campus test suite (277 tests, all passing at the start) was removed with the
features it covered. From NEVO only the Cohere embedding *pattern* (1536-dimension check, fail loudly
on mismatch) was followed; the code is our own.

### Rules the code enforces (and tests prove)

* **History is append-only.** No update/delete API; in PostgreSQL a trigger rejects UPDATE/DELETE and
  `complaints` rows with history cannot be deleted (no cascade).
* **Every state change and its history row are one transaction** (verified by a rollback test on PostgreSQL).
* **Status machine:** Pending → Under Process/Transferred/Escalated; Under Process → Transferred/Escalated/Disposed;
  Transferred → Pending (receiving bureau accepts); Escalated → Under Process/Disposed;
  Disposed → Under Process (reason required). Under Process needs an officer; Disposed needs a remark.
* **Visibility:** anyone sees *that* another bureau has a case at an institution (bureau, category,
  status, dates). Title, description, remarks, documents and history are visible only to the owning
  bureau, a super admin, or a bureau with a **confirmed** overlap, and viewing never implies editing.
  Decided in `services/access_service.py`, not in templates. Regional users only see their region.
* **The AI never acts.** It raises links; a bureau admin of an involved bureau or a super admin
  confirms, dismisses or marks them related-but-distinct, with a required reason, recorded in both
  case histories. A dismissed pair is not re-raised unless a description changes.
* **The AICTE ID is the only automatic join.** A name alone only creates an alias *suggestion*.
* **Citizens** see their own complaints only, in plain language, with internal remarks, officer names
  and other bureaus withheld (a whitelist of history entries, tested).

## Overlap detection

1. Candidates: other non-disposed complaints at the same `institution_id`.
2. Score each pair: semantic (Cohere embed-v4.0, both sides embedded as `search_document`, optional)
   and lexical (always computed; a transparent domain-concept keyword cosine that also yields the
   shared terms shown as evidence). Falls back to lexical on any API failure; every AI call is
   audited (model, latency, success).
3. Classify: `overlapping_proceeding` (high, different bureaus, both active: **alert**),
   `possible_duplicate` (high, same bureau), `related` (medium), `same_institution` (low: stored,
   shown on the profile, never alerts). Different-category pairs must clear a higher bar.

### Measured quality (the only numbers we claim)

`python -m scripts.eval_overlap` on `tests/data/overlap_pairs.json`: **52 synthetic pairs**
(26 same allegation, 26 not, including hard negatives such as "unauthorized exam fee" vs "development
fee not in prospectus").

| Offline lexical threshold | Precision | Recall | TP / FP / FN |
|---|---|---|---|
| 0.20 (related) | 0.83 | 0.96 | 25 / 5 / 1 |
| **0.30 (alert)** | **0.88** | **0.81** | 21 / 3 / 5 |
| 0.40 | 1.00 | 0.65 | 17 / 0 / 9 |

Thresholds were chosen on this same set, so they are optimistic; the set is small and written by us.
**Embedding mode has not been measured** (no key available), so its thresholds are unvalidated
starting values. A test keeps lexical precision ≥ 0.85 and recall ≥ 0.75 on the set.

## Tests

* 105 offline tests: lifecycle and permissions, every visibility case, citizen privacy, overlap
  classification and review, embeddings (fake client: success, failure fallback, dimension
  mismatch), SLA and idempotent notifications, dashboard numbers vs stored data, reports and CSV
  injection, the seeded dataset, the **complete judge demo over HTTP**, the mobile API.
* 8 PostgreSQL tests (`TEST_DATABASE_URL`): schema and JSONB, the append-only trigger, foreign-key
  protection, atomic rollback, overlap and review, the seeded app. All 8 pass against a local
  PostgreSQL 18; **not run against a hosted database** (Supabase/Neon).
* Browser-checked: login, dashboard, overlap review, citizen filing with the institution search,
  case detail and its modals; every official and citizen page was measured at 390 px wide for
  horizontal overflow (none after fixes).

## Not done / limitations

PDF generation server-side (browser print-to-PDF works), LLM summaries, pgvector, email/SMS
notifications, citizen identity verification, virus scanning of uploads, image analysis from
NEVO/AreaPulse, CSRF tokens (SameSite cookies only), shared (multi-process) login throttling,
SQL-side filtering for very large data sets. See DEMO_GUIDE.md section 6.

## Configuration

See `.env.example`. Every value is optional for the offline demo. Production
(`APP_ENV=production`) refuses to start without real `SECRET_KEY`/`JWT_SECRET`/`DATABASE_URL` and an
`INITIAL_ADMIN_*` account, and never seeds demo data.

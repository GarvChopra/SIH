"""Domain constants for SAMANVAY (AICTE complaint monitoring). Stdlib-only."""

BRAND = {
    "product": "SAMANVAY",
    "name": "AICTE – Integrated Complaint Monitoring",
    "tagline": "Cross-Bureau Coordination System",
    "org": "All India Council for Technical Education",
    "short": "AICTE",
}

# ── complaint classification ───────────────────────────────────────────────
# category -> days allowed for disposal (SLA window)
SLA_DAYS = {
    "Unauthorized fees": 30,
    "Faculty qualifications": 45,
    "Infrastructure deficiency": 60,
    "Admission irregularities": 30,
    "Approval violations": 45,
    "Harassment": 15,
    "Certificates withheld": 20,
    "Examination irregularities": 30,
}
CATEGORIES = list(SLA_DAYS)
DEFAULT_SLA_DAYS = 30

PRIORITIES = ["low", "medium", "high", "critical"]

# ── status machine ─────────────────────────────────────────────────────────
PENDING, UNDER_PROCESS, TRANSFERRED, ESCALATED, DISPOSED = (
    "Pending", "Under Process", "Transferred", "Escalated", "Disposed")
STATUSES = [PENDING, UNDER_PROCESS, TRANSFERRED, ESCALATED, DISPOSED]

STATUS_TRANSITIONS = {
    PENDING:       [UNDER_PROCESS, TRANSFERRED, ESCALATED],
    UNDER_PROCESS: [TRANSFERRED, ESCALATED, DISPOSED],
    TRANSFERRED:   [PENDING],            # accepted by the receiving bureau
    ESCALATED:     [UNDER_PROCESS, DISPOSED],
    DISPOSED:      [UNDER_PROCESS],      # reopen, reason required
}
ACTIVE_STATUSES = [s for s in STATUSES if s != DISPOSED]

# ── roles ──────────────────────────────────────────────────────────────────
SUPER_ADMIN, BUREAU_ADMIN, OFFICER, REGIONAL_USER, CITIZEN = (
    "super_admin", "bureau_admin", "officer", "regional_user", "citizen")
STAFF_ROLES = [SUPER_ADMIN, BUREAU_ADMIN, OFFICER, REGIONAL_USER]
ROLES = STAFF_ROLES + [CITIZEN]
ROLE_LABELS = {
    SUPER_ADMIN: "Super Admin", BUREAU_ADMIN: "Bureau Admin",
    OFFICER: "Officer", REGIONAL_USER: "Regional User", CITIZEN: "Citizen",
}

# Citizen complaints are received by this cell; it can transfer them onward.
INTAKE_BUREAU_CODE = "PGRC"

# Case-history entries a complainant may see, with the wording shown to them.
# Internal remarks, officer names and bureau-to-bureau detail are never exposed.
CITIZEN_VISIBLE_ACTIONS = {
    "registered": "Complaint received",
    "assigned": "Assigned to an officer",
    "status_changed": "Examination started",
    "transferred": "Forwarded to the concerned AICTE bureau",
    "transfer_accepted": "Accepted by the concerned AICTE bureau",
    "escalated": "Escalated for senior review",
    "disposed": "Case disposed",
    "reopened": "Case reopened",
    "citizen_update": "Update from AICTE",
}

REGIONS = ["North", "South", "East", "West", "Central"]

# code, name, region (None = headquarters cell)
BUREAUS = [
    ("RB",    "Regulation Bureau",                  None),
    ("PGRC",  "Public Grievance Redressal Cell",    None),
    ("AB",    "Approval Bureau",                    None),
    ("AC",    "Academic Cell",                      None),
    ("VC",    "Vigilance Cell",                     None),
    ("RO-N",  "Northern Regional Office",           "North"),
    ("RO-S",  "Southern Regional Office",           "South"),
]

# ── case actions / overlap model ───────────────────────────────────────────
SAME_INSTITUTION = "same_institution"
RELATED = "related"
POSSIBLE_DUPLICATE = "possible_duplicate"
OVERLAPPING_PROCEEDING = "overlapping_proceeding"
RELATIONSHIP_TYPES = [SAME_INSTITUTION, RELATED, POSSIBLE_DUPLICATE, OVERLAPPING_PROCEEDING]

PENDING_REVIEW, CONFIRMED, DISMISSED, RELATED_DISTINCT = (
    "pending", "confirmed", "dismissed", "related_distinct")
REVIEW_STATUSES = [PENDING_REVIEW, CONFIRMED, DISMISSED, RELATED_DISTINCT]
REVIEW_DECISIONS = [CONFIRMED, DISMISSED, RELATED_DISTINCT]

# Similarity thresholds. Embeddings (Cohere embed-v4.0) and the offline lexical
# fallback score on different scales, so each mode has its own pair.
# LEXICAL pair: chosen with `python -m scripts.eval_overlap` on the 52 labelled
# synthetic pairs in tests/data/overlap_pairs.json (0.30 -> precision 0.88 / recall
# 0.81; 0.20 -> best F1 0.89). Tuned in-sample, so optimistic.
# EMBED pair: starting values only, NOT yet measured (no API key was available).
EMBED_HIGH, EMBED_MEDIUM = 0.80, 0.65
LEXICAL_HIGH, LEXICAL_MEDIUM = 0.30, 0.20
# Complaints filed under DIFFERENT categories must clear a higher bar to be called an
# overlapping proceeding (the domain lexicon is broad, e.g. "hostel" and "laboratory"
# both map to infrastructure). Below the raised bar they are demoted to "related".
CROSS_CATEGORY_MARGIN = {"lexical": 0.25, "embedding": 0.08, "llm": 0.15}
# LLM-judge mode (Groq): the model answers "same allegation?" on a 0..1 scale. Starting
# values, NOT measured. Pairs the keyword matcher scores below LLM_PREFILTER are not sent
# to the model at all (clearly unrelated; saves latency, cost and exposure of text).
LLM_HIGH, LLM_MEDIUM, LLM_PREFILTER = 0.75, 0.50, 0.12
EMBED_MODEL = "embed-v4.0"
EMBED_DIMENSION = 1536

# ── SLA ────────────────────────────────────────────────────────────────────
DUE_SOON_FRACTION = 0.20          # last 20% of the disposal window
ON_TRACK, DUE_SOON, OVERDUE = "on_track", "due_soon", "overdue"

# ── numbering ──────────────────────────────────────────────────────────────
CODE_PREFIX = "AICTE-CMP-"
CODE_PAD = 5

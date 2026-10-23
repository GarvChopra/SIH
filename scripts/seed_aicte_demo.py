"""Synthetic SAMANVAY demo dataset. Everything here is invented: no real AICTE
institutions, IDs, complaints, or people.

    python -m scripts.seed_aicte_demo            # seed the configured database, print accounts
    python -m scripts.seed_aicte_demo --accounts # only print the demo accounts

`build()` is also called at startup in offline (in-memory) mode. It is idempotent:
if institutions already exist it does nothing.

How history gets realistic timestamps: case history is append-only and stamped
with the current time, so the seeder runs the *real* services under a temporary
clock that it moves backwards and forwards. Nothing is written around the
services, so seeded data obeys every rule live data does (state machine, history,
overlap detection, notifications).
"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DAY = 86400.0

# aicte id, name, address, region
INSTITUTIONS = [
    ("123456", "ABC Engineering College", "Greater Noida, Uttar Pradesh", "North"),
    ("654321", "Southern Institute of Technology", "Chennai, Tamil Nadu", "South"),
    ("200001", "Demo Institute of Technology", "Bhopal, Madhya Pradesh", "Central"),
    ("100101", "Himalayan College of Engineering", "Dehradun, Uttarakhand", "North"),
    ("100102", "Sutlej Institute of Technology", "Ludhiana, Punjab", "North"),
    ("100103", "Aravali Technical Campus", "Gurugram, Haryana", "North"),
    ("100104", "Gangetic Polytechnic College", "Lucknow, Uttar Pradesh", "North"),
    ("100105", "Deccan Institute of Engineering", "Hyderabad, Telangana", "South"),
    ("100106", "Malabar College of Technology", "Kozhikode, Kerala", "South"),
    ("100107", "Cauvery Engineering Institute", "Bengaluru, Karnataka", "South"),
    ("100108", "Nilgiri Institute of Management", "Coimbatore, Tamil Nadu", "South"),
    ("100109", "Konkan School of Engineering", "Ratnagiri, Maharashtra", "West"),
    ("100110", "Sahyadri Institute of Technology", "Pune, Maharashtra", "West"),
    ("100111", "Thar Polytechnic Institute", "Jodhpur, Rajasthan", "West"),
    ("100112", "Narmada College of Engineering", "Surat, Gujarat", "West"),
    ("100113", "Kalinga Institute of Applied Science", "Bhubaneswar, Odisha", "East"),
    ("100114", "Brahmaputra Technical Institute", "Guwahati, Assam", "East"),
    ("100115", "Hooghly College of Technology", "Kolkata, West Bengal", "East"),
    ("100116", "Magadh Institute of Engineering", "Patna, Bihar", "East"),
    ("100117", "Vindhya Institute of Technology", "Indore, Madhya Pradesh", "Central"),
    ("100118", "Satpura College of Engineering", "Nagpur, Maharashtra", "Central"),
    ("100119", "Bundelkhand Technical College", "Jhansi, Uttar Pradesh", "Central"),
    ("100120", "Chambal Polytechnic", "Gwalior, Madhya Pradesh", "Central"),
    ("100121", "Yamuna Institute of Pharmacy", "Agra, Uttar Pradesh", "North"),
    ("100122", "Western Ghats College of Architecture", "Mumbai, Maharashtra", "West"),
    ("100123", "Coromandel Institute of Technology", "Visakhapatnam, Andhra Pradesh", "South"),
    ("100124", "Eastern Hills Institute of Technology", "Shillong, Meghalaya", "East"),
    ("100125", "Lakeside College of Engineering", "Udaipur, Rajasthan", "West"),
    ("100126", "Triveni Institute of Management Studies", "Prayagraj, Uttar Pradesh", "North"),
    ("100127", "Rayalaseema College of Engineering", "Tirupati, Andhra Pradesh", "South"),
]
QUERIES = [
    ("Is this college approved by AICTE?", "Please confirm whether this institution is currently approved by AICTE for the engineering programmes it advertises."),
    ("Refund rules after withdrawing admission", "Which rules decide how much fee a college must refund if a student withdraws before the session starts?"),
    ("How to verify a faculty member's qualification", "What documents can a student ask for to check that a faculty member meets the minimum qualification norms?"),
    ("Maximum fee a college can charge", "Where can I find the fee structure approved for this college and the programme I am joining?"),
    ("Status of my earlier application", "I applied for a transfer certificate last month. Whom should I contact at the college and what is the time limit?"),
    ("Is hostel accommodation compulsory?", "The college says hostel stay is compulsory in the first year. Is that allowed under AICTE norms?"),
    ("Which bureau handles approval issues?", "Which AICTE bureau should I write to about a course that is advertised but not listed as approved?"),
    ("Lateral entry admission rules", "Can a diploma holder join the second year directly and which documents does the college need to check?"),
]
RESERVED = {"123456", "200001"}        # planted scenario / left empty for the live demo

TEMPLATES = {
    "Unauthorized fees": [
        ("Extra fee demanded before hall tickets", "The college is asking every student to pay Rs {amt} on top of the notified tuition before releasing hall tickets. No receipt is issued for this amount."),
        ("Development fee not in prospectus", "A yearly development fee of Rs {amt} is collected from students although it was never mentioned in the admission prospectus or the approved fee structure."),
        ("Fee hiked after admission", "The management raised the annual fee by Rs {amt} after admissions were complete and refuses to show a written order approving the increase."),
        ("Lab and library charges beyond norms", "Students are charged Rs {amt} per semester for laboratory and library use, far above what the approved fee structure allows."),
    ],
    "Faculty qualifications": [
        ("Faculty without required degrees", "Several lecturers in the {dept} department do not hold the postgraduate degree needed to teach at this level, yet they are shown as qualified in the college records."),
        ("Teaching staff exist only on paper", "The college reports more than {n} faculty members to inspectors, but many of them never attend classes and appear to be listed only on paper."),
        ("Unqualified visiting teachers", "Classes for {dept} are handled by visiting teachers who do not meet the minimum qualification and experience norms."),
    ],
    "Infrastructure deficiency": [
        ("Laboratories lack equipment", "The {dept} laboratories do not have most of the equipment the approved curriculum needs, so students cannot complete practical work."),
        ("Library has almost no books", "The central library has very few textbooks for the current syllabus and no access to journals despite the conditions of approval."),
        ("Unsafe hostel building", "The hostel building has cracked walls and no working fire safety equipment, and the rooms are badly overcrowded."),
        ("No safe drinking water", "There is no safe drinking water on campus and the toilets in the academic block remain locked or unusable."),
    ],
    "Admission irregularities": [
        ("Seats offered against donation", "The management takes a donation of Rs {amt} for management quota seats and tells students they will not get a place otherwise."),
        ("Admitted without entrance score", "Students without a valid entrance exam score were admitted while candidates on the merit list were refused seats."),
        ("Intake above sanctioned seats", "The college admitted about {n} more students than the intake sanctioned by AICTE this year."),
    ],
    "Approval violations": [
        ("Programme run without approval", "The college offers a new {dept} programme that has no AICTE approval and advertises it widely."),
        ("Approval has lapsed", "The approval lapsed at the end of the last academic year but the institution continues to admit new students."),
        ("Closed branch still running", "A branch closed in the approval records still runs classes and collects fees without any oversight."),
    ],
    "Harassment": [
        ("Ragging in the hostel", "First year students report repeated ragging by seniors in the hostel and the warden has taken no action."),
        ("Harassment by a staff member", "A staff member harasses female students and the internal complaints committee ignores written complaints."),
        ("Threats over internal marks", "A professor threatens to fail students in internal assessment unless they agree to personal demands."),
    ],
    "Certificates withheld": [
        ("Original certificates withheld", "Original degree certificates are held back after course completion until extra dues of Rs {amt} are cleared."),
        ("Marksheets not issued", "The college refuses to issue marksheets and a transfer certificate to students who left the programme."),
        ("Documents retained at admission", "Original school marksheets taken at admission are not returned even after the student withdrew."),
    ],
    "Examination irregularities": [
        ("Question paper circulated early", "Question papers for the semester examination were circulating among some students before the exam date."),
        ("Results delayed for months", "Semester results are delayed by months and students are given no revaluation process."),
        ("Impersonation in examinations", "Students are reportedly allowed to write examinations on behalf of others in exchange for money."),
    ],
}
BUREAU_FOR = {
    "Unauthorized fees": ["PGRC", "RB"], "Faculty qualifications": ["AC", "RB"],
    "Infrastructure deficiency": ["AB", "AC"], "Admission irregularities": ["PGRC", "VC"],
    "Approval violations": ["AB", "RB"], "Harassment": ["PGRC", "VC"],
    "Certificates withheld": ["PGRC"], "Examination irregularities": ["AC", "VC"],
}
DEPTS = ["Mechanical", "Civil", "Electronics", "Computer Science", "Electrical", "Chemical"]
OUTCOMES = ["Directed the institution to comply and submit a report within 15 days.",
            "Inspection completed; the institution has rectified the issue.",
            "Show-cause notice issued; compliance verified.",
            "Refund of the amount collected has been ordered.",
            "Complaint examined; corrective action taken and verified."]
CITIZEN_OUTCOMES = ["Your complaint was examined. The college has been directed to withdraw the charge and refund what was collected.",
                    "AICTE inspected the institution and the issue has been corrected. Thank you for bringing it to our notice.",
                    "A show-cause notice was issued to the institution and compliance has been verified."]

_real_time = time.time


def _clock(t):
    time.time = lambda: t


def _restore():
    time.time = _real_time


def _actor(username):
    from db import users
    u = users.get_by_username(username)
    return {"id": u["id"], "username": u["username"], "display_name": u["display_name"],
            "role": u["role"], "bureau_id": u["bureau_id"], "region": u["region"],
            "department": u["department"]}


# staff who do the work in each bureau: (admin, [officers])
STAFF = {"PGRC": ("pgrc.admin", ["pgrc.officer", "pgrc.officer2"]),
         "RB": ("rb.admin", ["rb.officer", "rb.officer2"]),
         "AB": ("ab.admin", ["ab.officer"]), "AC": ("ac.admin", ["ac.officer"]),
         "VC": ("vc.admin", ["vc.officer"]), "RO-N": ("ron.admin", ["ron.user"]),
         "RO-S": ("ros.admin", ["ros.user"])}


def _fill(tpl, rng):
    t, d = tpl
    kw = dict(amt=rng.choice([1500, 2500, 3000, 5000, 7500, 10000, 15000]),
              dept=rng.choice(DEPTS), n=rng.choice([20, 30, 45, 60, 80]))
    return t, d.format(**kw)


def _code_to_id(code):
    from db import bureaus
    return bureaus.get_by_code(code)["id"]


def _file(rng, now, ago_days, inst, cat, bureau_code, text=None, aicte=True, name_only=None):
    """Create one complaint through the real service, at `ago_days` in the past."""
    from services import complaint_service as cs
    admin, _ = STAFF[bureau_code]
    t = now - ago_days * DAY
    _clock(t)
    title, desc = text or _fill(rng.choice(TEMPLATES[cat]), rng)
    kw = dict(title=title, description=desc, category=cat, received_at=t,
              priority=rng.choice(["low", "medium", "medium", "high", "critical"]))
    if name_only:
        return cs.create(_actor(admin), institution_name=name_only, **kw)
    return cs.create(_actor(admin), aicte_id=inst["aicte_permanent_id"],
                     institution_name=(name_only or None), **kw)


def _advance(rng, now, c, outcome):
    """Move a freshly filed case through part of its life, under the temporary clock."""
    from db import bureaus, complaints
    from services import complaint_service as cs
    code = bureaus.get(c["bureau_id"])["code"]
    admin, officers = STAFF[code]
    received = c["received_at"]

    def at(days):
        t = min(received + days * DAY, now - 60)
        _clock(max(t, received + 60))

    def assign_and_start():
        cur = complaints.get(c["id"])
        at(rng.uniform(0.3, 3))
        cs.assign(_actor(admin), c["id"], _actor(rng.choice(officers))["id"])
        at(rng.uniform(3, 6))
        cs.change_status(_actor(admin), c["id"], "Under Process")
        if rng.random() < 0.6:
            at(rng.uniform(6, 9))
            cs.add_remark(_actor(admin), c["id"], rng.choice(
                ["Notice sent to the institution seeking its reply.",
                 "Documents received from the complainant and verified.",
                 "Institution has asked for more time to respond.",
                 "Site inspection scheduled with the regional office."]))

    if outcome == "pending":
        return
    if outcome == "under_process":
        assign_and_start()
    elif outcome == "escalated":
        assign_and_start()
        at(rng.uniform(10, 14))
        cs.escalate(_actor(admin), c["id"], "Matter needs senior review: repeated non-response from the institution.")
    elif outcome in ("transferred", "transferred_accepted"):
        target = rng.choice([b for b in ("RB", "AB", "AC", "VC", "PGRC") if b != code])
        at(rng.uniform(1, 4))
        cs.transfer(_actor(admin), c["id"], _code_to_id(target),
                    f"Falls within the remit of {target}.")
        if outcome == "transferred_accepted":
            t_admin, t_officers = STAFF[target]
            at(rng.uniform(5, 7))
            cs.accept_transfer(_actor(t_admin), c["id"])
            at(rng.uniform(7, 9))
            cs.assign(_actor(t_admin), c["id"], _actor(rng.choice(t_officers))["id"])
            at(rng.uniform(9, 12))
            cs.change_status(_actor(t_admin), c["id"], "Under Process")
    elif outcome == "disposed":
        assign_and_start()
        # most close inside the SLA, some late: so compliance is not a flat 100%
        from services import sla_service
        sla_days = (sla_service.disposal_deadline(c["category"], received) - received) / DAY
        late = rng.random() < 0.25
        at(sla_days * (rng.uniform(1.05, 1.5) if late else rng.uniform(0.35, 0.9)))
        cur = complaints.get(c["id"])
        who = _actor(admin)
        msg = rng.choice(CITIZEN_OUTCOMES) if cur.get("citizen_id") else None
        cs.change_status(who, c["id"], "Disposed", rng.choice(OUTCOMES), msg)


def _pick_outcome(rng, age_days, sla_days):
    """Older cases are mostly closed; only a few are left to run past their deadline."""
    weights = [("pending", 20), ("under_process", 27), ("escalated", 6), ("transferred", 5),
               ("transferred_accepted", 7), ("disposed", 35)]
    if age_days < 12:
        weights = [("pending", 60), ("under_process", 40)]
    elif age_days > sla_days * 0.8:
        weights = [("disposed", 72), ("under_process", 9), ("escalated", 8), ("pending", 3),
                   ("transferred", 3), ("transferred_accepted", 5)]
    r = rng.uniform(0, sum(w for _, w in weights))
    for name, w in weights:
        r -= w
        if r <= 0:
            return name
    return "pending"


def build(seed: int = 7) -> dict:
    from db import institutions, users
    if institutions.list_all():
        return {"skipped": "institutions already exist"}
    from db import seeds
    seeds.seed_essential()
    seeds.seed_demo_accounts()
    seeds.seed_demo_citizen()
    from services import auth_service, citizen_service as ctz, complaint_service as cs
    from services import overlap_service as ov

    rng = random.Random(seed)
    now = _real_time()
    taken = {}                                     # aicte id -> categories already used there
    made = {"planted": 0, "random": 0, "citizen": 0, "query": 0}
    ai_off = ov.ai_disabled()            # never call Cohere/Groq for hundreds of seeded pairs
    ai_off.__enter__()
    try:
        for aid, name, addr, region in INSTITUTIONS:
            institutions.create(aid, name, addr, region)
        inst = {i["aicte_permanent_id"]: i for i in institutions.list_all()}

        def use(aid, cat):
            taken.setdefault(aid, set()).add(cat)

        # ---------- planted scenario 1: ABC Engineering College (123456) ----------
        abc = inst["123456"]
        pgrc = _file(rng, now, 9, abc, "Unauthorized fees", "PGRC",
                     ("Unauthorized examination fees",
                      "Students are being charged unauthorized examination fees."))
        # RB staff name it differently and give the ID: same institution, different wording
        from services import complaint_service
        _clock(now - 8 * DAY)
        rb = complaint_service.create(
            _actor("rb.admin"), aicte_id="123456", institution_name="ABC Engg. College",
            title="Excess exam charges",
            description="The institution is collecting excess examination charges.",
            category="Unauthorized fees", priority="high", received_at=now - 8 * DAY)
        from db import institutions as _i
        _i.add_alias(abc["id"], "ABC Engg. College", status="approved")
        fq = _file(rng, now, 20, abc, "Faculty qualifications", "AC",
                   ("Faculty without required degrees",
                    "Several lecturers in the Mechanical department do not hold the postgraduate degree needed to teach at this level."))
        inf = _file(rng, now, 15, abc, "Infrastructure deficiency", "AB",
                    ("Laboratories lack equipment",
                     "The Electronics laboratories do not have most of the equipment the approved curriculum needs, so students cannot complete practical work."))
        for cat in ("Unauthorized fees", "Faculty qualifications", "Infrastructure deficiency"):
            use("123456", cat)
        _advance(rng, now, pgrc, "under_process")
        _advance(rng, now, fq, "under_process")
        _advance(rng, now, inf, "pending")
        made["planted"] += 4

        # ---------- planted pairs elsewhere (decided and pending) ----------
        def pair(aid, cat_a, bureau_a, text_a, cat_b, bureau_b, text_b, ago_a, ago_b):
            a = _file(rng, now, ago_a, inst[aid], cat_a, bureau_a, text_a)
            b = _file(rng, now, ago_b, inst[aid], cat_b, bureau_b, text_b)
            use(aid, cat_a); use(aid, cat_b)
            made["planted"] += 2
            return a, b

        a, b = pair("100101", "Admission irregularities", "VC",
                    ("Capitation fee for management seats", "Management took a capitation donation for management quota seats."),
                    "Admission irregularities", "PGRC",
                    ("Seats sold for donation", "Seats were sold for a donation during counselling at the college."), 30, 26)
        _clock(now - 22 * DAY)
        from db import case_links
        link = case_links.get_pair(a["id"], b["id"])
        ov.decide(_actor("vc.admin"), link["id"], "confirmed",
                  "Same donation racket reported to both VC and PGRC. VC will lead; PGRC shares its complainant list.")
        _advance(rng, now, a, "under_process")
        _advance(rng, now, b, "under_process")

        a, b = pair("100105", "Unauthorized fees", "PGRC",
                    ("Hostel charges doubled", "Hostel charges were doubled in the middle of the academic year."),
                    "Unauthorized fees", "RB",
                    ("Mid-year hostel fee increase", "Mid year the hostel fee was increased to twice the amount."), 24, 21)
        _clock(now - 18 * DAY)
        link = case_links.get_pair(a["id"], b["id"])
        ov.decide(_actor("rb.admin"), link["id"], "dismissed",
                  "Checked with PGRC: these concern two different hostels run by separate trusts.")
        _advance(rng, now, a, "under_process")

        a, b = pair("100110", "Examination irregularities", "AC",
                    ("Question paper leak", "Question papers were leaked before the semester examination."),
                    "Examination irregularities", "VC",
                    ("Semester paper circulated early", "The semester paper was leaked to some students before the examination."), 6, 4)
        _advance(rng, now, a, "pending")

        # a name-only complaint: waits for a human to approve the suggested institution
        _clock(now - 3 * DAY)
        cs.create(_actor("rb.admin"), institution_name="Southern Inst. of Technology",
                  title="Certificates not issued", category="Certificates withheld",
                  description="The institute has not issued degree certificates to the last batch.",
                  received_at=now - 3 * DAY)
        use("654321", "Certificates withheld")
        made["planted"] += 1

        # ---------- the bulk: ~140 complaints across institutions ----------
        plan = []
        for aid, *_ in INSTITUTIONS:
            if aid in RESERVED:
                continue
            cats = [c for c in TEMPLATES if c not in taken.get(aid, set())]
            rng.shuffle(cats)
            for cat in cats[:rng.randint(4, 6)]:
                plan.append((rng.uniform(4, 115), aid, cat))
        plan.sort(reverse=True)                       # oldest first: detection sees history in order
        for ago, aid, cat in plan:
            region = inst[aid]["region"]
            options = list(BUREAU_FOR[cat])
            if region == "North" and rng.random() < 0.25:
                options = ["RO-N"]
            elif region == "South" and rng.random() < 0.25:
                options = ["RO-S"]
            code = rng.choice(options)
            c = _file(rng, now, ago, inst[aid], cat, code)
            use(aid, cat)
            from domain.constants import SLA_DAYS
            _advance(rng, now, c, _pick_outcome(rng, ago, SLA_DAYS[cat]))
            made["random"] += 1

        # ---------- Tier 1 queries: questions answered by the helpdesk (PGRC) ----------
        from domain.constants import SLA_DAYS
        spots = [aid for aid, *_ in INSTITUTIONS if aid not in RESERVED]
        for _i in range(24):
            aid, ago = rng.choice(spots), rng.uniform(1, 40)
            title, desc = rng.choice(QUERIES)
            _clock(now - ago * DAY)
            q = cs.create(_actor("pgrc.admin"), aicte_id=aid, title=title, description=desc,
                          tier=1, priority="low", received_at=now - ago * DAY)
            _advance(rng, now, q, _pick_outcome(rng, ago, 7))
            made["query"] += 1

        # ---------- citizens ----------
        citizens = {}
        for uname, full in [("priya.nair", "Priya Nair"), ("rohan.mehta", "Rohan Mehta"),
                            ("sneha.iyer", "Sneha Iyer"), ("kabir.singh", "Kabir Singh")]:
            _clock(now - 60 * DAY)
            citizens[uname] = ctz.register(uname, full, "demo12345", f"{uname}@example.invalid")
        citizens["citizen.demo"] = users.get_by_username("citizen.demo")

        def citizen_case(user, aid, cat, ago, outcome, text=None, tier=2):
            if tier == 2 and cat in taken.get(aid, set()):
                cat = next(c for c in TEMPLATES if c not in taken.get(aid, set()))
            u = citizens[user]
            actor = {"id": u["id"], "username": u["username"], "display_name": u["display_name"],
                     "role": "citizen", "bureau_id": None, "region": None, "department": None}
            _clock(now - ago * DAY)
            title, desc = text or (rng.choice(QUERIES) if tier == 1 else _fill(rng.choice(TEMPLATES[cat]), rng))
            c = cs.create_for_citizen(actor, aicte_id=aid, category=cat, tier=tier, title=title,
                                      description=desc)
            if tier == 2:
                use(aid, cat)
            _advance(rng, now, c, outcome)
            if outcome == "under_process":
                _clock(min(now - 60, c["received_at"] + 9 * DAY))
                from db import complaints as _cm
                cs.post_citizen_update(_actor("pgrc.officer"), c["id"],
                                       "We have written to the institution and asked it to respond within 15 days.") \
                    if _cm.get(c["id"])["officer_id"] == _actor("pgrc.officer")["id"] else None
            made["citizen"] += 1
            return c

        citizen_case("citizen.demo", "100103", "Unauthorized fees", 2, "pending",
                     ("Extra fee for hall tickets", "The college is asking every student to pay Rs 5000 on top of the notified tuition before releasing hall tickets. No receipt is issued."))
        citizen_case("citizen.demo", "100104", "Certificates withheld", 14, "under_process")
        citizen_case("citizen.demo", "100107", "Harassment", 48, "disposed")
        citizen_case("priya.nair", "100106", "Approval violations", 11, "under_process")
        citizen_case("priya.nair", "100112", "Faculty qualifications", 33, "disposed")
        citizen_case("rohan.mehta", "100114", "Infrastructure deficiency", 5, "pending")
        citizen_case("rohan.mehta", "100118", "Unauthorized fees", 27, "under_process")
        citizen_case("sneha.iyer", "100120", "Examination irregularities", 19, "under_process")
        citizen_case("kabir.singh", "100122", "Admission irregularities", 8, "pending")
        citizen_case("citizen.demo", "100108", None, 3, "pending", tier=1)
        citizen_case("priya.nair", "100110", None, 16, "disposed", tier=1)
    finally:
        _restore()
        ai_off.__exit__(None, None, None)

    from db import complaints, pool
    if pool.is_firestore():
        pool.flush_to_firestore()
    return {"institutions": len(INSTITUTIONS), "complaints": len(complaints.all_rows()), **made}


def print_accounts() -> None:
    from db import seeds
    print("\nDEMO ACCOUNTS (synthetic)")
    print(f"{'username':<16}{'role':<15}{'bureau':<7}{'name':<22}credential")
    for a in seeds.DEMO_ACCOUNTS:
        print(f"{a['username']:<16}{a['role']:<15}{(a['bureau'] or '-'):<7}{a['name']:<22}PIN {a['pin']}")
    print(f"{'citizen.demo':<16}{'citizen':<15}{'-':<7}{'Aarav Sharma':<22}password citizen123")
    print(f"{'priya.nair':<16}{'citizen':<15}{'-':<7}{'Priya Nair':<22}password demo12345 (also rohan.mehta, sneha.iyer, kabir.singh)")
    print("\nReserved for the live demo: 'Demo Institute of Technology' (AICTE ID 200001) has no cases.")
    print("Planted overlap: ABC Engineering College (AICTE ID 123456): PGRC and RB exam-fee cases.\n")


def main() -> int:
    if "--accounts" in sys.argv:
        print_accounts()
        return 0
    from app import create_app
    os.environ.setdefault("SEED_DEMO_DATA", "0")
    create_app()
    print("seed:", build())
    print_accounts()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# SAMANVAY: Demo Guide

AICTE Integrated Complaint Monitoring & Cross-Bureau Coordination System.
Everything in the demo is **synthetic**: no real AICTE institutions, IDs, complaints or people.

## 1. Run it (Windows, no database, no API keys)

```powershell
cd aicte-dashboard
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

Open **http://127.0.0.1:5000**. With no `.env` at all the app starts on an in-memory store and
seeds itself: 30 institutions, about 165 complaints, 7 bureaus, 17 staff accounts, 5 citizens.
Startup takes a few seconds (password hashing). Nothing needs editing by hand during the demo.

* **Reset the demo:** stop the server (Ctrl+C) and start it again. Everything is re-seeded.
* **Another port:** `set PORT=5055` before starting.
* **Persistent data (optional):** set `DATABASE_URL` to a **fresh** PostgreSQL database
  (it will not migrate the old campus schema), then run
  `python -m scripts.seed_aicte_demo` once. To reseed, drop and recreate the database.
* **Optional Groq AI judge:** put `GROQ_API_KEY=...` in a `.env` file (copy `.env.example`) and restart. Groq has no embeddings, so it scores each similar pair 0 to 1 as "same allegation?" and adds a one-line reason on the review screen. Pairs the keyword matcher finds clearly unrelated are not sent. Any failure falls back to offline matching. Unmeasured; test it once before presenting because filing a case then waits on the network (8 s timeout).
* **Optional AI embeddings (takes priority over Groq):** `pip install cohere==5.15.0` and set `COHERE_API_KEY`. Without it the
  offline keyword matcher is used (this is what the demo is tested with).
* **Show all accounts:** `python -m scripts.seed_aicte_demo --accounts`

## 2. Accounts

The login page lists the staff accounts and fills them in when clicked (development mode only).

| Who | Username | Credential | Role / bureau |
|---|---|---|---|
| Super admin | `superadmin` | PIN `1234` | sees everything, manages users |
| PGRC admin | `pgrc.admin` | PIN `1234` | Public Grievance Redressal Cell |
| PGRC officer | `pgrc.officer`, `pgrc.officer2` | PIN `1234` | PGRC |
| RB admin | `rb.admin` | PIN `1234` | Regulation Bureau |
| RB officer | `rb.officer`, `rb.officer2` | PIN `1234` | RB |
| Other bureaus | `ab.admin`/`ab.officer`, `ac.admin`/`ac.officer`, `vc.admin`/`vc.officer` | PIN `1234` | Approval, Academic, Vigilance |
| Regional | `ron.admin`, `ron.user` (North), `ros.admin`, `ros.user` (South) | PIN `1234` | regional offices |
| **Citizen** | `citizen.demo` | password `citizen123` | has 3 complaints already |
| Citizens | `priya.nair`, `rohan.mehta`, `sneha.iyer`, `kabir.singh` | password `demo12345` | |

## 3. What is pre-seeded for the demo

* **ABC Engineering College (AICTE ID 123456):** PGRC ("unauthorized examination fees") and RB
  ("excess exam charges", stated as "ABC Engg. College") already carry a **pending overlap alert**.
  The same college also has a faculty-qualification case (AC) and an infrastructure case (AB), which
  must **not** raise an alert (the negative example).
* Two decided examples (one confirmed, one dismissed) and one more pending alert (exam-paper leak).
* One complaint filed by **name only** (no AICTE ID): it waits under *Name matches*.
* **Demo Institute of Technology (ID 200001) is empty.** Use it for the live walkthrough so the
  alert appears in front of the judges.

## 4. The judge demonstration (about 10 minutes)

Use two browser windows (or one normal and one private) so the citizen and the officials stay signed in.

1. **Dashboard.** Sign in as `superadmin`. Point out: live KPI tiles (each number is a link to the
   filtered case list), the overlap-alert banner, bureau-wise and region-wise workload, top
   institutions, overdue and due-soon counts.
2. **Citizen signs in.** Window 2: `citizen.demo` / `citizen123`. Show the citizen dashboard (3
   complaints with progress bars, notifications).
3. **Citizen files.** *File a complaint* → search "demo" → pick *Demo Institute of Technology* →
   category *Unauthorized fees* → title "Students charged unauthorized examination fees" →
   description (30+ characters, e.g. "The college is collecting an extra Rs 4000 from every student as
   unauthorized examination fees and gives no receipt.") → optionally attach a file → Submit.
4. **Tracking ID.** The confirmation page shows the ID (for example `AICTE-CMP-00165`), a progress
   tracker (Received → Under examination → Action taken → Closed) and *Copy*.
5. **PGRC picks it up.** Window 1: sign out, sign in as `pgrc.admin`. *Cases* → status *Pending*:
   the citizen's case is there (marked "Citizen complaint"). Open it → **Assign officer** →
   `Kavita Menon` → **Update status** → *Under Process* (remark: any text) → **Message the
   complainant**: "We have written to the institute."
6. **RB files a related complaint.** Sign in as `rb.officer` → **New case** → AICTE ID `200001`,
   category *Unauthorized fees*, title "Excess exam charges", description "The institution is
   collecting excess examination charges." → Register.
7. **Overlap detected automatically.** Sign in as `rb.admin`: a notification and **Overlap review (n)**
   appear. The wording is different, the shared evidence is not (fee, unauthorized, exam).
8. **Review.** Open the alert: both cases side by side, the matching evidence checklist, the scores,
   and the shared terms. Note that the PGRC case's text is hidden from RB until a decision.
   (Super admin sees both.)
9. **Decide.** Type a reason ("Same fee demand; PGRC and RB to coordinate") → **Confirm overlap**.
   Open either case: the decision is in **both** histories. RB can now read the PGRC case
   (read-only); nothing was merged or changed by the AI.
10. **Act on the cases.** As `rb.admin`: assign `Vikram Chauhan`, **Escalate** with a reason. As
    `pgrc.admin`: **Update status** → *Disposed* with an internal remark and an *outcome message for
    the complainant* ("The institute has been directed to refund the extra fee."). Also show
    **Transfer** (receiving bureau must **Accept**) and, as an officer, **Request transfer/escalation**.
11. **Statistics update.** `superadmin` → Dashboard: totals, status split, bureau workload and
    overlap count changed. *Notifications* shows assignments, transfers, escalations and overlap decisions.
12. **Citizen sees only what is permitted.** Window 2: refresh the complaint: status *Closed*, the
    outcome message, and the update trail. They never see internal remarks, officer names, other
    bureaus or other people's complaints.
13. **Monitoring.** *Institutions* → *Demo Institute of Technology*: both cases, the confirmed link,
    the bureaus involved. *Reports*: bureau-wise, region-wise, institution-wise, ageing,
    disposal rate, deadline compliance, overlap. Open one, **CSV** or **Print / save as PDF**.
14. **Negative example.** Open ABC Engineering College (`123456`): four cases, only the fee pair is
    flagged; the faculty and infrastructure cases are listed but raise no alert.

**Visual Regulatory Workflow Management (board view).** *Cases* → **Board**: one column per status,
cards show case number, bureau, institution, category, officer, deadline (overdue in red), an
*Overlap flagged* tag and a *Citizen* tag. Use **PGRC board** for a bureau-specific board. Drag a card
to a column: a form asks for what that move needs (a reason, a receiving bureau, or an outcome message
for a citizen), and the server validates it exactly as on the case page and writes the history entry.
Cards you may not move are not draggable; other bureaus' cards show as restricted. Drag-and-drop needs a
mouse; on a phone, open the card and use its buttons.

Extras if time allows: *Name matches* (approve the suggested institution for the name-only case and
watch detection run), *Admin → Users / Bureaus / Audit log* (super admin), the mobile layout
(shrink the window or open it on a phone), `/api/v1` for the mobile app.

## 5. Features completed

* Institutions registry keyed by AICTE permanent ID; name-only complaints get alias suggestions that
  a human approves (never auto-linked).
* Bureaus, 4 staff roles + citizens, per-case visibility and edit rules enforced in the service layer.
* Case lifecycle with append-only history, SLA states, notifications, documents.
* Overlap detection (offline keyword matching, optional Cohere embeddings), human review with a
  mandatory reason, dismissed pairs never re-raised unless the text changes.
* Citizen portal (register, search institution, file with evidence, track, notifications), responsive.
* Mobile JSON API (`/api/v1`, bearer tokens) over the same services.
* Kanban board view (drag-and-drop status changes through the same validated actions) beside the table.
* Dashboard, filters, seven reports with CSV and print-to-PDF, audit log, user and bureau admin.

## 6. Known limitations (say these before a judge finds them)

* **The AI similarity is a suggestion.** Offline keyword matching on 52 labelled synthetic pairs:
  precision 0.88 / recall 0.81 at the alert threshold (0.30), tuned on the same pairs, so it is
  optimistic. Cohere embedding mode is implemented and tested with a fake client but **has not been
  measured** (no API key); its thresholds (0.80 / 0.65) are starting values.
* Complaints in different categories need a higher score to be flagged (an explicit rule, because the
  keyword lexicon is broad).
* In-memory mode loses data on restart and supports one process only.
* Notifications are in-app only (no email/SMS). Citizen accounts are self-registered with no email
  verification. Uploaded files are type- and size-checked but not virus-scanned and are stored on
  local disk (`uploads/`).
* PDF is via the browser's *Save as PDF*; there is no server-side PDF.
* Filtering is done in Python over the loaded tables: fine at demo scale, not for hundreds of
  thousands of cases.
* Not ported from NEVO/AreaPulse: image verification, duplicate-image detection and clustering
  (they need API keys, so they cannot be part of a dependable offline demo).
* No CSRF tokens: protection relies on SameSite cookies. Login throttling is per-process.
* This is a prototype with synthetic data; it is not an official AICTE system.

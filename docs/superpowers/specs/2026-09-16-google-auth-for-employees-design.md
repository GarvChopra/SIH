# Google Sign-In for Employees (Faculty)

## Goal
Faculty ("employee") login stops using username + PIN and becomes
Google Sign-In only. Admin login is unaffected — still username + PIN.

## Scope (explicitly minimal, per user)
- No Google Workspace domain restriction — any Google account can sign in.
- First-time sign-in auto-creates a `reporter` account from the Google
  profile (email + name). No admin pre-registration step.
- Returning email → logs into the existing account. No duplicate created.
- Nothing else changes: routing, classification, admin dashboard, etc.
  are untouched.

## Data model
`users` table (additive migration, same pattern already used in
`db/schema.py`):
- `email TEXT UNIQUE` — nullable; only Google-authenticated users have it.
- `pin_hash` becomes nullable (`ALTER COLUMN pin_hash DROP NOT NULL`) —
  Google-authenticated reporters have no PIN.

Admin rows are untouched (still `username` + `pin_hash`, no `email`).

## Auth flow
- Login page, Employee segment: PIN form replaced by a **"Sign in with
  Google"** button linking to `/auth/google/login`. Admin segment
  (username + PIN form, `/set-pin`) is untouched.
- `GET /auth/google/login` — if `GOOGLE_OAUTH_CLIENT_ID`/`SECRET` are
  configured, redirects to Google's OAuth consent screen
  (`openid email profile` scope) via Authlib. If not configured (local
  dev / test), renders a dev-only mock picker instead (see below).
- `GET /auth/google/callback` — Authlib exchanges the code and verifies
  the ID token, yielding `email` + `name`. Then:
  - existing user, role `reporter` → issue session cookies, log in.
  - no existing user → create a new `reporter` user (`email`, `name`),
    then log in.
  - existing user, role `admin` → reject: "Admins sign in with a
    username and PIN."
  - Uses the same `auth_service` JWT + cookie issuance as today, so
    every downstream route (`g.current_user`, role checks) is
    unchanged.
- `POST /login` (the PIN route) explicitly refuses role `reporter`
  from now on, so a stale bookmark/cached form can't PIN-login an
  employee.

## Dev / test mode
When Google OAuth env vars aren't set:
- `/auth/google/login` renders a small dev-only picker (seeded demo
  names, or type any email) that posts to
  `/auth/google/mock-callback`, which runs the exact same
  find-or-create + cookie logic as the real callback — no network
  call to Google.
- `/auth/google/mock-callback` hard-refuses (404) whenever
  `Config.is_production()` is true, so it can never be reachable in
  production even by accident.
- Existing tests get a small login helper that drives this mock
  instead of `POST /login` with a PIN, for faculty logins. Admin
  tests (`admin`/`0000` via PIN) are unchanged.

## Credentials
`GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` env vars
(same `.env` pattern as `GROQ_API_KEY` etc.). Obtained from Google
Cloud Console — driven live with the user present for any
account-changing step (consent screen, credential creation).
Redirect URIs to register: `https://unipulse-main-wdif.onrender.com/auth/google/callback`
and `http://localhost:5000/auth/google/callback`.

## New dependency
`Authlib` (added to `requirements.txt`).

## Out of scope (explicitly, per user)
- Domain restriction / Workspace-only sign-in.
- Admin approval or invite step before an employee can sign in.
- Linking a Google identity to an already-existing username+PIN
  faculty account (a returning email that doesn't match one of those
  legacy accounts just gets a fresh account, same as any other
  first-time sign-in).

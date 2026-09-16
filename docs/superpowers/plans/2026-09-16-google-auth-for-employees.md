# Google Sign-In for Employees Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Employee (faculty/`reporter`) login becomes Google Sign-In only — PIN login for that role is removed at the service layer, not just hidden in the UI. Admin login (username + PIN) is untouched.

**Architecture:** Reuse the existing JWT-cookie session machinery unchanged. Add one new auth_service function that finds-or-creates a `reporter` row by email (the row's `username` column holds the lowercased email — no schema migration needed) and issues the same tokens `login()` issues today. Add three routes to the existing `auth` blueprint: a real Google OAuth redirect/callback pair (via Authlib) that's a no-op unless `GOOGLE_OAUTH_CLIENT_ID`/`SECRET` are set, plus a dev-only mock callback that exercises the exact same find-or-create-login code path without calling Google, so local dev and the test suite never need real credentials.

**Tech Stack:** Flask, Authlib (new dependency) for the OAuth/OIDC client, existing PyJWT + bcrypt session stack.

**Spec:** `docs/superpowers/specs/2026-09-16-google-auth-for-employees-design.md`

## Global Constraints

- No new `users` table columns and no Postgres migration — a Google-authenticated reporter's `username` column holds their lowercased email; `pin_hash` is set to a random, never-revealed value purely to satisfy the existing `NOT NULL` constraint.
- No Google Workspace domain restriction — any Google account may sign in (per spec).
- First Google sign-in with an unrecognized email auto-creates a `reporter` account; a recognized email logs into the existing row — no duplicates (per spec).
- Admin login (username + PIN, `/set-pin`, `/admin/settings/pin`) is completely unchanged.
- The dev-only mock Google login (`/auth/google/mock-callback`) must be unreachable whenever `Config.is_production()` is true, or whenever real Google credentials ARE configured — checked in the route itself, not just by convention.
- Until the last task, only run the test file(s) each task directly touches — `POST /login` will reject `reporter` logins starting in Task 2, and dozens of pre-existing tests rely on the old behavior. The full suite is only expected green again after Task 5.

---

### Task 1: Dependency + config flags

**Files:**
- Modify: `requirements.txt`
- Modify: `config.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_google_auth.py` (new file)

**Interfaces:**
- Produces: `Config.GOOGLE_OAUTH_CLIENT_ID: str`, `Config.GOOGLE_OAUTH_CLIENT_SECRET: str`, `Config.google_oauth_configured() -> bool` — every later task's "is Google configured?" checks call this classmethod.

- [ ] **Step 1: Write the failing test**

Create `tests/test_google_auth.py`:

```python
"""Employee Google Sign-In: config gate, find-or-create, and route behavior."""
from config import Config


def test_google_oauth_not_configured_by_default():
    assert Config.google_oauth_configured() is False


def test_google_oauth_configured_when_both_set(monkeypatch):
    monkeypatch.setattr(Config, "GOOGLE_OAUTH_CLIENT_ID", "id123")
    monkeypatch.setattr(Config, "GOOGLE_OAUTH_CLIENT_SECRET", "secret123")
    assert Config.google_oauth_configured() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_google_auth.py -v`
Expected: FAIL — `AttributeError: type object 'Config' has no attribute 'google_oauth_configured'`

- [ ] **Step 3: Add the config flags**

In `config.py`, inside `class Config:`, add next to the other optional-integration keys (near `GROQ_API_KEY`):

```python
    GOOGLE_OAUTH_CLIENT_ID     = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
```

And add this classmethod next to `is_production`:

```python
    @classmethod
    def google_oauth_configured(cls) -> bool:
        return bool(cls.GOOGLE_OAUTH_CLIENT_ID and cls.GOOGLE_OAUTH_CLIENT_SECRET)
```

- [ ] **Step 4: Keep the test suite isolated from real Google credentials**

In `tests/conftest.py`, add `"GOOGLE_OAUTH_CLIENT_ID"` and `"GOOGLE_OAUTH_CLIENT_SECRET"` to the tuple of env vars blanked at the top of the file (same reasoning as the existing entries — a real `.env` value must never leak into a test run):

```python
for _k in ("DATABASE_URL", "GROQ_API_KEY", "RESEND_API_KEY", "IMAGEKIT_PRIVATE_KEY",
           "FIREBASE_KEY_JSON", "FIREBASE_CREDENTIALS_B64",
           "FIREBASE_SERVICE_ACCOUNT_JSON", "GOOGLE_APPLICATION_CREDENTIALS",
           "R2_ACCOUNT_ID", "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_BUCKET_NAME",
           "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "S3_BUCKET_NAME",
           "INITIAL_ADMIN_USERNAME", "INITIAL_ADMIN_PIN",
           "ALLOW_PROD_DEMO_SEED", "SEED_DEMO", "FLASK_DEBUG",
           "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"):
    os.environ[_k] = ""
```

- [ ] **Step 5: Add Authlib to requirements**

In `requirements.txt`, add this line after `PyJWT==2.8.0`:

```
Authlib==1.3.2
```

Then install it: `pip install Authlib==1.3.2`

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_google_auth.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add requirements.txt config.py tests/conftest.py tests/test_google_auth.py
git commit -m "Add Google OAuth config flags and Authlib dependency"
```

---

### Task 2: `login_with_google` in auth_service, and reporter PIN login rejection

**Files:**
- Modify: `services/auth_service.py`
- Test: `tests/test_google_auth.py`

**Interfaces:**
- Consumes: `db.users.get_by_username(username) -> dict | None`, `db.users.create(username, display_name, role, pin_hash, department=None, contact=None, created_by=None, must_change_pin=False) -> dict`, `hash_pin(pin: str) -> str`, `create_access_token(user: dict) -> str`, `create_refresh_token(user_id: str, days: int = 7) -> str` — all already defined in this file.
- Produces: `auth_service.login_with_google(email: str, display_name: str) -> LoginResult`, used by the routes added in Task 3. `auth_service.login(username, pin)` now returns `LoginResult(False, ...)` for any non-`admin` role, regardless of PIN — every caller in the codebase already handles a failed `LoginResult` the same way it does today, so no other file needs to change for this step.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_google_auth.py`:

```python
from db import users
from services import auth_service
from services.auth_service import hash_pin


def test_login_with_google_creates_new_reporter(memstore):
    result = auth_service.login_with_google("new.prof@gmail.com", "New Prof")
    assert result.success
    u = users.get_by_username("new.prof@gmail.com")
    assert u is not None
    assert u["role"] == "reporter"
    assert u["display_name"] == "New Prof"
    assert u["contact"] == "new.prof@gmail.com"


def test_login_with_google_reuses_existing_account_by_email(memstore):
    first = auth_service.login_with_google("same@gmail.com", "Same Person")
    second = auth_service.login_with_google("same@gmail.com", "Same Person Again")
    assert first.user["id"] == second.user["id"]
    assert len([u for u in users.list_all() if u["username"] == "same@gmail.com"]) == 1


def test_login_with_google_rejects_admin_account(memstore):
    users.create("admin@example.com", "Admin", "admin", hash_pin("0000"))
    result = auth_service.login_with_google("admin@example.com", "Admin")
    assert result.success is False


def test_login_with_google_rejects_inactive_account(memstore):
    u = users.create("gone@gmail.com", "Gone", "reporter", hash_pin("x"))
    users.set_active(u["id"], False)
    result = auth_service.login_with_google("gone@gmail.com", "Gone")
    assert result.success is False


def test_pin_login_rejects_reporter_role(memstore):
    users.create("prof.demo", "Prof Demo", "reporter", hash_pin("1234"))
    result = auth_service.login("prof.demo", "1234")
    assert result.success is False
    assert "Google" in result.error


def test_pin_login_still_works_for_admin(memstore):
    users.create("boss", "Boss", "admin", hash_pin("0000"))
    result = auth_service.login("boss", "0000")
    assert result.success is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_google_auth.py -v`
Expected: `test_login_with_google_*` FAIL with `AttributeError: module 'services.auth_service' has no attribute 'login_with_google'`; `test_pin_login_rejects_reporter_role` FAILS because `login()` currently accepts any role.

- [ ] **Step 3: Add `import secrets` to auth_service.py**

In `services/auth_service.py`, change:

```python
import time
from dataclasses import dataclass, field
```

to:

```python
import secrets
import time
from dataclasses import dataclass, field
```

- [ ] **Step 4: Reject non-admin roles in `login()`**

In `services/auth_service.py`, change:

```python
def login(username: str, pin: str) -> LoginResult:
    from db import users
    u = users.get_by_username((username or "").lower().strip())
    if not u or not u.get("is_active", True):
        return LoginResult(False, error="No such account")
    if not verify_pin(pin, u["pin_hash"]):
        return LoginResult(False, error="Incorrect PIN")
    return LoginResult(
        True, user=u,
        access_token=create_access_token(u),
        refresh_token=create_refresh_token(u["username"]),
    )
```

to:

```python
def login(username: str, pin: str) -> LoginResult:
    from db import users
    u = users.get_by_username((username or "").lower().strip())
    if not u or not u.get("is_active", True):
        return LoginResult(False, error="No such account")
    if u["role"] != "admin":
        return LoginResult(False, error="Employees sign in with Google — use the Employee tab.")
    if not verify_pin(pin, u["pin_hash"]):
        return LoginResult(False, error="Incorrect PIN")
    return LoginResult(
        True, user=u,
        access_token=create_access_token(u),
        refresh_token=create_refresh_token(u["username"]),
    )
```

- [ ] **Step 5: Add `login_with_google`**

In `services/auth_service.py`, add this function directly after `login()`:

```python
def login_with_google(email: str, display_name: str) -> LoginResult:
    """Find-or-create a reporter by Google account email, then issue the same
    session tokens login() issues. The row's `username` column holds the
    lowercased email — there is no separate email column. A newly-created
    row gets a random, never-revealed PIN hash purely to satisfy the
    database's NOT NULL constraint; login() above refuses PIN login for any
    non-admin role, so that hash is never checked against anything."""
    from db import users
    email = (email or "").strip().lower()
    if not email:
        return LoginResult(False, error="Google did not share an email address.")
    u = users.get_by_username(email)
    if u:
        if not u.get("is_active", True):
            return LoginResult(False, error="Account inactive")
        if u["role"] != "reporter":
            return LoginResult(False, error="That account signs in with a username and PIN.")
    else:
        u = users.create(email, display_name or email, "reporter",
                         hash_pin(secrets.token_hex(16)), contact=email)
    return LoginResult(
        True, user=u,
        access_token=create_access_token(u),
        refresh_token=create_refresh_token(u["username"], days=30),
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_google_auth.py -v`
Expected: PASS (8 tests)

- [ ] **Step 7: Commit**

```bash
git add services/auth_service.py tests/test_google_auth.py
git commit -m "Add login_with_google; reject reporter-role PIN login"
```

---

### Task 3: OAuth routes and app wiring

**Files:**
- Modify: `blueprints/auth/__init__.py`
- Modify: `app.py`
- Test: `tests/test_google_auth.py`

**Interfaces:**
- Consumes: `auth_service.login_with_google(email, display_name) -> LoginResult` (Task 2), `Config.google_oauth_configured() -> bool` (Task 1), existing `set_session_cookies` / `_set_cookies` alias in this same file.
- Produces: `GET /auth/google/login`, `GET /auth/google/callback`, `POST /auth/google/mock-callback` routes; `init_google_oauth(app)` function, called once from `app.py`. Task 4 links to `/auth/google/login` from the login page and renders `templates/auth/google_mock.html` from the mock branch of `google_login()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_google_auth.py`:

```python
def test_google_login_route_shows_dev_mock_when_not_configured(client):
    r = client.get("/auth/google/login")
    assert r.status_code == 200
    assert b'action="/auth/google/mock-callback"' in r.data


def test_google_mock_callback_creates_account_and_logs_in(client):
    r = client.post("/auth/google/mock-callback",
                    data={"email": "mock.new@gmail.com", "name": "Mock New"})
    assert r.status_code == 302 and r.headers["Location"] == "/"
    assert client.get("/my-reports/data").status_code == 200   # session cookie is live
    assert users.get_by_username("mock.new@gmail.com") is not None


def test_google_mock_callback_reuses_existing_account(client):
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "ignored"})
    me = client.get("/profile").data
    assert b"Prof. Meera Rao" in me   # the seeded prof.rao row, not a new one


def test_google_mock_callback_404s_when_google_is_configured(client, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, "GOOGLE_OAUTH_CLIENT_ID", "id123")
    monkeypatch.setattr(Config, "GOOGLE_OAUTH_CLIENT_SECRET", "secret123")
    r = client.post("/auth/google/mock-callback", data={"email": "x@gmail.com", "name": "X"})
    assert r.status_code == 404


def test_google_mock_callback_404s_in_production(client, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, "APP_ENV", "production")
    r = client.post("/auth/google/mock-callback", data={"email": "x@gmail.com", "name": "X"})
    assert r.status_code == 404


def test_pin_login_route_rejects_reporter(client):
    r = client.post("/login", data={"username": "prof.rao", "pin": "1234"})
    assert r.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_google_auth.py -v`
Expected: The four `test_google_*` route tests FAIL with 404 (routes don't exist yet); `test_pin_login_route_rejects_reporter` already PASSES (Task 2 covered the service layer, which the route already calls).

- [ ] **Step 3: Add the OAuth client and route handlers**

In `blueprints/auth/__init__.py`, change the import line:

```python
from flask import Blueprint, g, make_response, redirect, render_template, request, url_for
```

to:

```python
from authlib.integrations.flask_client import OAuth
from flask import Blueprint, abort, g, make_response, redirect, render_template, request, url_for
```

Add this right after `bp = Blueprint("auth", __name__, template_folder="../../templates")`:

```python
oauth = OAuth()


def init_google_oauth(app):
    """Register Google as an OAuth provider. No-op when credentials aren't
    configured — google_login() below then serves the dev-only mock picker
    instead of a real Google redirect."""
    if not Config.google_oauth_configured():
        return
    oauth.init_app(app)
    oauth.register(
        name="google",
        client_id=Config.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=Config.GOOGLE_OAUTH_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
```

Add these routes at the end of the file (after the existing `refresh()` view):

```python
def _finish_google_login(email: str, name: str):
    result = auth_service.login_with_google(email, name)
    if not result.success:
        return render_template("login.html", error=result.error), 401
    resp = make_response(redirect("/"))
    return _set_cookies(resp, result.access_token, result.refresh_token, remember=True)


@bp.get("/auth/google/login")
def google_login():
    if g.get("current_user"):
        return redirect("/admin" if g.current_user["role"] == "admin" else "/")
    if not Config.google_oauth_configured():
        if Config.is_production():
            abort(404)
        return render_template("auth/google_mock.html")
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@bp.get("/auth/google/callback")
def google_callback():
    if not Config.google_oauth_configured():
        abort(404)
    token = oauth.google.authorize_access_token()
    userinfo = token.get("userinfo") or {}
    return _finish_google_login(userinfo.get("email"), userinfo.get("name"))


@bp.post("/auth/google/mock-callback")
def google_mock_callback():
    if Config.is_production() or Config.google_oauth_configured():
        abort(404)
    return _finish_google_login(request.form.get("email"), request.form.get("name"))
```

- [ ] **Step 4: Create the dev-only mock picker template**

Create `templates/auth/google_mock.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Sign in with Google (dev) &middot; UNIFIX</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='css/app.css') }}">
</head>
<body>
<div class="auth-wrap">
  <div class="auth-card" style="max-width:440px;margin:40px auto">
    <h1>Dev-only Google sign-in</h1>
    <p class="sub">GOOGLE_OAUTH_CLIENT_ID isn't configured, so this stands in for the
       real Google consent screen — local/dev use only, never shown in production.</p>
    <form method="post" action="/auth/google/mock-callback">
      <label for="email">Email (or a seeded demo username, e.g. prof.rao)</label>
      <input id="email" name="email" required placeholder="you@example.com">
      <label for="name">Name</label>
      <input id="name" name="name" placeholder="Your name">
      <p style="margin-top:18px"><button type="submit" class="btn">Continue</button></p>
    </form>
  </div>
</div>
</body>
</html>
```

- [ ] **Step 5: Wire `init_google_oauth` into the app factory and fix HTTPS detection behind Render's proxy**

In `app.py`, change:

```python
from flask import Flask, g, redirect, request
```

to:

```python
from flask import Flask, g, redirect, request
from werkzeug.middleware.proxy_fix import ProxyFix
```

Change:

```python
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = Config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
```

to:

```python
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = Config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
    # Render terminates TLS and forwards over plain HTTP internally. Without this,
    # url_for(..., _external=True) — used to build the Google OAuth redirect_uri —
    # would generate an http:// URL in production and Google would reject it as a
    # redirect_uri mismatch. Only trust the proxy's scheme/host, not x_for, since
    # existing rate-limiting reads request.remote_addr directly.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
```

Change:

```python
    from blueprints.auth import bp as auth_bp
    app.register_blueprint(auth_bp)
```

to:

```python
    from blueprints.auth import bp as auth_bp, init_google_oauth
    app.register_blueprint(auth_bp)
    init_google_oauth(app)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_google_auth.py -v`
Expected: PASS (14 tests)

- [ ] **Step 7: Commit**

```bash
git add blueprints/auth/__init__.py app.py templates/auth/google_mock.html tests/test_google_auth.py
git commit -m "Add Google OAuth login/callback routes and dev-only mock login"
```

---

### Task 4: Login page — Google button for employees

**Files:**
- Modify: `templates/login.html`
- Test: `tests/test_google_auth.py`

**Interfaces:**
- Consumes: `/auth/google/login` (Task 3).
- Produces: no new interfaces — this is leaf-level UI.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_google_auth.py`:

```python
def test_login_page_offers_google_for_employees_not_pin(client):
    html = client.get("/login").data
    assert b'href="/auth/google/login"' in html
    assert b'id="admin-block" hidden' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_google_auth.py -v`
Expected: FAIL — neither string is in the current page.

- [ ] **Step 3: Replace the employee PIN form with a Google sign-in button**

In `templates/login.html`, change:

```html
    <div class="seg" role="tablist">
      <button type="button" class="on" id="seg-faculty" onclick="segTo('faculty')">Employee</button>
      <button type="button" id="seg-admin" onclick="segTo('admin')">Admin</button>
    </div>

    {% if error %}<p class="error" style="margin-bottom:12px">{{ error }}</p>{% endif %}

    <form method="post" action="/login">
      <label for="username" id="ul">Username</label>
      <input id="username" name="username" autocomplete="username" required placeholder="prof.name">
      <label for="pin">PIN</label>
      <input id="pin" name="pin" type="password" inputmode="numeric"
             autocomplete="current-password" required placeholder="••••">
      <label class="check"><input type="checkbox" name="remember" value="1"> Remember me on this device</label>
      <p style="margin-top:18px"><button type="submit" class="btn">Sign in</button></p>
    </form>

    <p class="tiny muted" id="hint" style="text-align:center;margin-top:14px">
      Employee accounts are created by the campus admin.</p>
```

to:

```html
    <div class="seg" role="tablist">
      <button type="button" class="on" id="seg-faculty" onclick="segTo('faculty')">Employee</button>
      <button type="button" id="seg-admin" onclick="segTo('admin')">Admin</button>
    </div>

    {% if error %}<p class="error" style="margin-bottom:12px">{{ error }}</p>{% endif %}

    <div id="employee-block">
      <p style="margin:22px 0"><a class="btn" href="/auth/google/login">Sign in with Google</a></p>
      <p class="tiny muted" id="hint" style="text-align:center">
        Sign in with your college Google account. Your first sign-in creates your account automatically.</p>
    </div>

    <div id="admin-block" hidden>
      <form method="post" action="/login">
        <label for="username" id="ul">Admin ID</label>
        <input id="username" name="username" autocomplete="username" required placeholder="admin">
        <label for="pin">PIN</label>
        <input id="pin" name="pin" type="password" inputmode="numeric"
               autocomplete="current-password" required placeholder="••••">
        <label class="check"><input type="checkbox" name="remember" value="1"> Remember me on this device</label>
        <p style="margin-top:18px"><button type="submit" class="btn">Sign in</button></p>
      </form>
      <p class="tiny muted" style="text-align:center;margin-top:14px">Secure &middot; Transparent &middot; Efficient</p>
    </div>
```

- [ ] **Step 4: Update the segment-toggle script**

In `templates/login.html`, change:

```html
function segTo(w){
  const admin = w === 'admin';
  document.getElementById('seg-faculty').classList.toggle('on', !admin);
  document.getElementById('seg-admin').classList.toggle('on', admin);
  document.body.classList.toggle('admin-login', admin);
  document.getElementById('brandline').textContent = admin ? 'UNIFIX Admin' : 'UNIFIX';
  document.getElementById('welcome').textContent = admin ? 'Admin sign-in' : 'Welcome back';
  document.getElementById('portal').textContent = admin
    ? 'Campus Super Admin Portal' : 'Campus Grievance & Infrastructure Portal';
  document.getElementById('ul').textContent = admin ? 'Admin ID' : 'Username';
  document.getElementById('username').placeholder = admin ? 'admin' : 'prof.name';
  document.getElementById('hint').textContent = admin
    ? 'Secure · Transparent · Efficient' : 'Employee accounts are created by the campus admin.';
}
```

to:

```html
function segTo(w){
  const admin = w === 'admin';
  document.getElementById('seg-faculty').classList.toggle('on', !admin);
  document.getElementById('seg-admin').classList.toggle('on', admin);
  document.body.classList.toggle('admin-login', admin);
  document.getElementById('employee-block').hidden = admin;
  document.getElementById('admin-block').hidden = !admin;
  document.getElementById('brandline').textContent = admin ? 'UNIFIX Admin' : 'UNIFIX';
  document.getElementById('welcome').textContent = admin ? 'Admin sign-in' : 'Welcome back';
  document.getElementById('portal').textContent = admin
    ? 'Campus Super Admin Portal' : 'Campus Grievance & Infrastructure Portal';
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_google_auth.py -v`
Expected: PASS (15 tests)

- [ ] **Step 6: Commit**

```bash
git add templates/login.html tests/test_google_auth.py
git commit -m "Login page: Google sign-in button for employees, PIN kept for admin"
```

---

### Task 5: Remove the employee PIN-change feature; migrate the rest of the suite

**Why this is one task:** Task 2 already made `POST /login` reject `reporter` logins. Every test file below still logs employees in the old way, so the full suite is red from Task 2 onward. This task removes the now-meaningless employee PIN-change feature and fixes every remaining call site in one pass, ending with a full green run — splitting it further would leave an intermediate task deliberately broken.

**Files:**
- Modify: `blueprints/faculty/__init__.py` (remove `/profile/pin`)
- Modify: `templates/faculty/profile.html` (remove the "Change PIN" section)
- Modify: `blueprints/admin/__init__.py` (admin can no longer reset a reporter's PIN)
- Modify: `tests/test_faculty_ui.py`, `tests/test_admin_user_mgmt.py`, `tests/test_admin_portal_pages.py`, `tests/test_admin_rbac.py`, `tests/test_admin_reports.py`, `tests/test_admin_routes.py`, `tests/test_analytics.py`, `tests/test_dashboard_service.py`, `tests/test_faculty_routes.py`, `tests/test_pwa_offline.py`, `tests/test_queue_sort.py`, `tests/test_session_security.py`, `tests/test_account_deletion.py`

**Interfaces:**
- Consumes: `POST /auth/google/mock-callback` with form fields `email`, `name` (Task 3) — the shared shape every test-file edit below uses. Passing an existing seeded username (e.g. `"prof.rao"`) as `email` logs into that exact seeded row, since `login_with_google` looks up by the `username` column regardless of whether the value looks like a real address.

#### Part A — remove the employee PIN-change feature

- [ ] **Step 1: Remove the route**

In `blueprints/faculty/__init__.py`, change:

```python
from services.auth_service import hash_pin, verify_pin
```

to (drop the now-unused import):

```python
```

(i.e. delete that whole line)

Then delete this entire block:

```python
@bp.post("/profile/pin")
def profile_pin():
    u = users.get_by_username(g.current_user["username"])
    cur = (request.form.get("current") or "").strip()
    new = (request.form.get("new") or "").strip()
    if not verify_pin(cur, u["pin_hash"]):
        return redirect("/profile?err=Current+PIN+is+incorrect")
    if not (new.isdigit() and 4 <= len(new) <= 8):
        return redirect("/profile?err=New+PIN+must+be+4-8+digits")
    users.set_pin(u["id"], hash_pin(new))
    audit.add(u["username"], "user.self_pin", target_type="user", target_id=u["id"])
    return redirect("/profile?ok=1")


```

(leave the blank line before `@bp.post("/profile/delete")` as the single separator)

- [ ] **Step 2: Remove the "Change PIN" section from the profile page**

In `templates/faculty/profile.html`, delete:

```html
<h3>Change PIN</h3>
<div class="card">
  <form method="post" action="/profile/pin">
    <label for="current">Current PIN</label>
    <input id="current" name="current" type="password" inputmode="numeric" required>
    <label for="new">New PIN (4–8 digits)</label>
    <input id="new" name="new" type="password" inputmode="numeric" pattern="\d{4,8}" required>
    <p style="margin-top:14px"><button class="btn" type="submit">Update PIN</button></p>
  </form>
</div>

```

- [ ] **Step 3: Stop admin from resetting a reporter's (nonexistent) PIN**

In `blueprints/admin/__init__.py`, change:

```python
@bp.post("/users/<int:uid>/pin")
@require_permission(USER_MANAGE)
def user_pin(uid):
    u = users.get_by_id(uid)
    if not u:
        abort(404)
    users.set_pin(uid, hash_pin(request.form["pin"].strip()))
    users.set_must_change_pin(uid, True)   # admin-reset PIN is temporary
    audit.add(_actor(), "user.reset_pin", target_type="user", target_id=uid,
              detail={"username": u["username"]})
    return redirect("/admin/users")
```

to:

```python
@bp.post("/users/<int:uid>/pin")
@require_permission(USER_MANAGE)
def user_pin(uid):
    u = users.get_by_id(uid)
    if not u:
        abort(404)
    if u["role"] != "admin":
        abort(400, description="Employees sign in with Google — there is no PIN to reset.")
    users.set_pin(uid, hash_pin(request.form["pin"].strip()))
    users.set_must_change_pin(uid, True)   # admin-reset PIN is temporary
    audit.add(_actor(), "user.reset_pin", target_type="user", target_id=uid,
              detail={"username": u["username"]})
    return redirect("/admin/users")
```

#### Part B — fix the two tests whose exact scenario just disappeared or moved

- [ ] **Step 4: `tests/test_faculty_ui.py`**

Change:

```python
def _login(c, u="prof.rao", p="1234", **extra):
    return c.post("/login", data={"username": u, "pin": p, **extra})


def test_profile_page_and_pin_change(client):
    _login(client)
    assert client.get("/profile").status_code == 200
    r = client.post("/profile/pin", data={"current": "1234", "new": "5678"})
    assert r.status_code == 302 and "ok=1" in r.headers["Location"]
    c2 = client.application.test_client()
    assert c2.post("/login", data={"username": "prof.rao", "pin": "1234"}).status_code == 401
    assert c2.post("/login", data={"username": "prof.rao", "pin": "5678"}).status_code == 302


def test_pin_change_rejects_bad_current(client):
    _login(client)
    r = client.post("/profile/pin", data={"current": "0000", "new": "5678"})
    assert "err=" in r.headers["Location"]
```

to:

```python
def _login(c, u="prof.rao", **extra):
    return c.post("/auth/google/mock-callback", data={"email": u, "name": u, **extra})


def test_profile_page_renders(client):
    _login(client)
    assert client.get("/profile").status_code == 200
```

- [ ] **Step 5: `tests/test_admin_user_mgmt.py`**

Change:

```python
def test_admin_pin_reset_forces_change(client):
    _login_admin(client)
    prof = users.get_by_username("prof.rao")
    client.post(f"/admin/users/{prof['id']}/pin", data={"pin": "5678"})
    assert users.get_by_id(prof["id"])["must_change_pin"] is True


def test_flagged_user_is_confined_to_set_pin(client):
    _login_admin(client)
    prof = users.get_by_username("prof.rao")
    client.post(f"/admin/users/{prof['id']}/pin", data={"pin": "5678"})
    c = client.application.test_client()
    c.post("/login", data={"username": "prof.rao", "pin": "5678"})
    # any normal page bounces to /set-pin
    r = c.get("/my-reports")
    assert r.status_code == 302 and "/set-pin" in r.headers["Location"]
    # setting a new PIN clears the flag
    c.post("/set-pin/submit", data={"new": "4321", "confirm": "4321"})
    assert users.get_by_id(prof["id"])["must_change_pin"] is False
    assert c.get("/my-reports").status_code == 200
```

to:

```python
def test_cannot_reset_a_reporters_pin(client):
    _login_admin(client)
    prof = users.get_by_username("prof.rao")
    r = client.post(f"/admin/users/{prof['id']}/pin", data={"pin": "5678"})
    assert r.status_code == 400
    assert users.get_by_id(prof["id"])["must_change_pin"] is False


def test_admin_pin_reset_forces_change(client, app):
    with app.app_context():
        second = users.create("sir2", "Sir Two", "admin", hash_pin("1234"))
    _login_admin(client)
    client.post(f"/admin/users/{second['id']}/pin", data={"pin": "5678"})
    assert users.get_by_id(second["id"])["must_change_pin"] is True


def test_flagged_admin_is_confined_to_set_pin(client, app):
    with app.app_context():
        second = users.create("sir2", "Sir Two", "admin", hash_pin("1234"))
    _login_admin(client)
    client.post(f"/admin/users/{second['id']}/pin", data={"pin": "5678"})
    c = client.application.test_client()
    c.post("/login", data={"username": "sir2", "pin": "5678"})
    # any normal page bounces to /set-pin
    r = c.get("/admin")
    assert r.status_code == 302 and "/set-pin" in r.headers["Location"]
    # setting a new PIN clears the flag
    c.post("/set-pin/submit", data={"new": "4321", "confirm": "4321"})
    assert users.get_by_id(second["id"])["must_change_pin"] is False
    assert c.get("/admin").status_code == 200
```

#### Part C — mechanical migration: reporter PIN login → Google mock login

The rule applied at every line below: a `POST /login` whose `username` is a *reporter* identity (`prof.rao`, `dr.iyer`, `prof.khan`, `prof.new`, or a helper's `user`/`u` parameter defaulting to one of those) becomes `POST /auth/google/mock-callback` with `email` set to that same identity and any `name` value (existing seeded rows are found by username/email and keep their real seeded display name; the `name` field is only used when a row doesn't exist yet). `admin` logins are never touched.

- [ ] **Step 6: `tests/test_admin_portal_pages.py`**

Change (3 occurrences, identical each time):

```python
    client.post("/login", data={"username": "prof.rao", "pin": "1234"})
```

to:

```python
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
```

at lines 29, 34, and 44.

- [ ] **Step 7: `tests/test_admin_rbac.py`**

Change:

```python
    client.post("/login", data={"username": "prof.rao", "pin": "1234"})
```

to:

```python
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
```

at line 4.

- [ ] **Step 8: `tests/test_admin_reports.py`**

Change:

```python
def test_reports_blocked_for_non_admin(client):
    client.post("/login", data={"username": "prof.rao", "pin": "1234"})
    assert client.get("/admin/reports").status_code == 403
```

to:

```python
def test_reports_blocked_for_non_admin(client):
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    assert client.get("/admin/reports").status_code == 403
```

- [ ] **Step 9: `tests/test_admin_routes.py`**

Change:

```python
def _report(client, user="prof.rao",
            desc="the ceiling fan is not working in this room"):
    c = client.application.test_client()
    c.post("/login", data={"username": user, "pin": "1234"})
```

to:

```python
def _report(client, user="prof.rao",
            desc="the ceiling fan is not working in this room"):
    c = client.application.test_client()
    c.post("/auth/google/mock-callback", data={"email": user, "name": user})
```

And change:

```python
def test_faculty_cannot_post_admin_action(client):
    out = _report(client)
    client.post("/login", data={"username": "prof.rao", "pin": "1234"})
    assert client.post(f"/admin/grievances/{out['code']}/verify").status_code == 403
```

to:

```python
def test_faculty_cannot_post_admin_action(client):
    out = _report(client)
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    assert client.post(f"/admin/grievances/{out['code']}/verify").status_code == 403
```

And change:

```python
def test_faculty_blocked_from_pulse(client):
    client.post("/login", data={"username": "prof.rao", "pin": "1234"})
    assert client.get("/admin/pulse").status_code == 403
```

to:

```python
def test_faculty_blocked_from_pulse(client):
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    assert client.get("/admin/pulse").status_code == 403
```

(Leave lines 107-109's `bad = client.application.test_client().post("/login", data={"username": "prof.new", "pin": "4321"}); assert bad.status_code == 401` untouched — a deactivated reporter now gets 401 from `/login` regardless of PIN, so the assertion still holds.)

- [ ] **Step 10: `tests/test_analytics.py`**

Change:

```python
def test_analytics_blocked_for_faculty(client):
    client.post("/login", data={"username": "prof.rao", "pin": "1234"})
    assert client.get("/admin/analytics").status_code == 403
```

to:

```python
def test_analytics_blocked_for_faculty(client):
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    assert client.get("/admin/analytics").status_code == 403
```

- [ ] **Step 11: `tests/test_dashboard_service.py`**

Change:

```python
def _report(client, user="prof.rao",
            desc="the ceiling fan is not working in this room again"):
    c = client.application.test_client()
    c.post("/login", data={"username": user, "pin": "1234"})
```

to:

```python
def _report(client, user="prof.rao",
            desc="the ceiling fan is not working in this room again"):
    c = client.application.test_client()
    c.post("/auth/google/mock-callback", data={"email": user, "name": user})
```

- [ ] **Step 12: `tests/test_faculty_routes.py`**

Change:

```python
def _login(client, u="prof.rao", p="1234"):
    return client.post("/login", data={"username": u, "pin": p})
```

to:

```python
def _login(client, u="prof.rao", p=None):
    return client.post("/auth/google/mock-callback", data={"email": u, "name": u})
```

Change:

```python
    other.post("/login", data={"username": "dr.iyer", "pin": "1234"})
```

to:

```python
    other.post("/auth/google/mock-callback", data={"email": "dr.iyer", "name": "Dr Iyer"})
```

Change:

```python
def test_base_page_links_manifest(client):
    client.post("/login", data={"username": "prof.rao", "pin": "1234"})
```

to:

```python
def test_base_page_links_manifest(client):
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
```

- [ ] **Step 13: `tests/test_pwa_offline.py`**

Change:

```python
def test_home_page_has_no_floating_install_button(client):
    client.post("/login", data={"username": "prof.rao", "pin": "1234"})
```

to:

```python
def test_home_page_has_no_floating_install_button(client):
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
```

- [ ] **Step 14: `tests/test_queue_sort.py`**

Change:

```python
def _report(client, user, desc, created_at=None):
    c = client.application.test_client()
    c.post("/login", data={"username": user, "pin": "1234"})
```

to:

```python
def _report(client, user, desc, created_at=None):
    c = client.application.test_client()
    c.post("/auth/google/mock-callback", data={"email": user, "name": user})
```

- [ ] **Step 15: `tests/test_routes_smoke.py`**

Change:

```python
def test_faculty_login_redirects_home(client):
    r = client.post("/login", data={"username": "prof.rao", "pin": "1234"})
    assert r.status_code == 302
    assert r.headers["Location"] == "/"
```

to:

```python
def test_faculty_login_redirects_home(client):
    r = client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    assert r.status_code == 302
    assert r.headers["Location"] == "/"
```

- [ ] **Step 16: `tests/test_session_security.py`**

Change:

```python
def _login(c, u="prof.rao", p="1234"):
    return c.post("/login", data={"username": u, "pin": p})
```

to:

```python
def _login(c, u="prof.rao", p="1234"):
    if u == "admin":
        return c.post("/login", data={"username": u, "pin": p})
    return c.post("/auth/google/mock-callback", data={"email": u, "name": u})
```

- [ ] **Step 17: `tests/test_account_deletion.py`**

Change:

```python
def _login(c, u="prof.rao", p="1234"):
    return c.post("/login", data={"username": u, "pin": p})
```

to:

```python
def _login(c, u="prof.rao", p="1234"):
    if u == "admin":
        return c.post("/login", data={"username": u, "pin": p})
    return c.post("/auth/google/mock-callback", data={"email": u, "name": u})
```

#### Part D — verify

- [ ] **Step 18: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS, 0 failures. If anything still fails, it is either a spot the file-by-file listing above missed or a genuinely new interaction — use `superpowers:systematic-debugging` rather than patching around it.

- [ ] **Step 19: Commit**

```bash
git add blueprints/faculty/__init__.py templates/faculty/profile.html blueprints/admin/__init__.py \
        tests/test_faculty_ui.py tests/test_admin_user_mgmt.py tests/test_admin_portal_pages.py \
        tests/test_admin_rbac.py tests/test_admin_reports.py tests/test_admin_routes.py \
        tests/test_analytics.py tests/test_dashboard_service.py tests/test_faculty_routes.py \
        tests/test_pwa_offline.py tests/test_queue_sort.py tests/test_session_security.py \
        tests/test_account_deletion.py
git commit -m "Remove employee PIN-change feature; migrate tests to Google mock login"
```

---

## After this plan: getting real credentials

Once the suite is green, the app works end-to-end in dev/test with the mock picker. To go live:
1. In Google Cloud Console (driven live with you present for any account-changing step): create an OAuth consent screen and an OAuth Client ID (type "Web application"), with authorized redirect URIs `https://unipulse-main-wdif.onrender.com/auth/google/callback` and `http://localhost:5000/auth/google/callback`.
2. Set `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` in `.env` (local) and in Render's environment settings (production) — never typed into any web form, only pasted into these two places.
3. Restart the app. `Config.google_oauth_configured()` flips to `True`, `/auth/google/login` now redirects to the real Google consent screen, and `/auth/google/mock-callback` self-disables (Task 3, Step 3).

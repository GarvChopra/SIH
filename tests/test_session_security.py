"""Phase 2 — sessions must reflect the current DB state, not a 15-minute-stale
JWT: deactivation revokes access now, role changes apply now, and a live
refresh token silently renews an expired access token."""
import time

import jwt

from config import Config
from db import users
from services import auth_service


def _login(c, u="prof.rao", p="1234"):
    if u == "admin":
        return c.post("/login", data={"username": u, "pin": p})
    return c.post("/auth/google/mock-callback", data={"email": u, "name": u})


def _expired_access(username, role="reporter", name="X"):
    now = int(time.time())
    return jwt.encode({"sub": username, "name": name, "role": role, "dept": None,
                       "iat": now - 4000, "exp": now - 1000, "type": "access"},
                      Config.JWT_SECRET, algorithm="HS256")


def test_deactivated_user_is_logged_out_on_next_request(client):
    _login(client)
    assert client.get("/").status_code == 200
    users.set_active(users.get_by_username("prof.rao")["id"], False)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_admin_demoted_to_reporter_loses_admin_access_immediately(client):
    _login(client, "admin", "0000")
    assert client.get("/admin/").status_code == 200
    # demote in the DB (role is generic reporter/admin — see rbac.py)
    admin = users.get_by_username("admin")
    if pool_is_memory():
        for u in _mem_users():
            if u["id"] == admin["id"]:
                u["role"] = "reporter"
    r = client.get("/admin/", follow_redirects=False)
    assert r.status_code in (302, 403)


def test_expired_access_token_auto_refreshes_from_refresh_cookie(client):
    _login(client)                       # sets a valid refresh cookie
    client.set_cookie("up_access", _expired_access("prof.rao"))
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    set_cookies = "\n".join(r.headers.getlist("Set-Cookie"))
    assert "up_access=" in set_cookies   # a fresh access cookie was issued


def test_expired_access_and_no_refresh_logs_out(client):
    client.set_cookie("up_access", _expired_access("prof.rao"))
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302 and "/login" in r.headers["Location"]


# -- helpers to poke the in-memory store used by the test client --
def pool_is_memory():
    from db import pool
    return pool.is_memory()


def _mem_users():
    from db import pool
    return pool.STATE["mem"]["users"]

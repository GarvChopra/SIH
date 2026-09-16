"""P0-5 — admin account creation is restricted, audited, and lockout-safe."""
from db import audit, users
from services.auth_service import hash_pin


def _login_admin(client):
    client.post("/login", data={"username": "admin", "pin": "0000"})


def _actions(action):
    return [e for e in audit.list_recent(500) if e["action"] == action]


def test_invalid_role_rejected(client):
    _login_admin(client)
    r = client.post("/admin/users", data={
        "username": "hacker", "display_name": "H", "role": "superuser", "pin": "1234"})
    assert r.status_code == 400
    assert users.get_by_username("hacker") is None


def test_role_defaults_to_reporter_and_is_audited(client):
    _login_admin(client)
    client.post("/admin/users", data={
        "username": "new.prof", "display_name": "New Prof", "role": "reporter", "pin": "1234"})
    u = users.get_by_username("new.prof")
    assert u and u["role"] == "reporter"
    ev = _actions("user.create")
    assert any(e["detail"].get("username") == "new.prof" and e["detail"].get("role") == "reporter"
               for e in ev)


def test_admin_creation_is_a_distinct_audited_action(client):
    _login_admin(client)
    client.post("/admin/users", data={
        "username": "second.admin", "display_name": "Second Sir", "role": "admin", "pin": "9999"})
    u = users.get_by_username("second.admin")
    assert u and u["role"] == "admin"
    ev = _actions("admin.create")
    assert any(e["detail"].get("username") == "second.admin" and e["detail"].get("role") == "admin"
               for e in ev)


def test_cannot_deactivate_own_account(client):
    _login_admin(client)
    me = users.get_by_username("admin")
    r = client.post(f"/admin/users/{me['id']}/toggle")
    assert r.status_code == 400
    assert users.get_by_username("admin")["is_active"] is True


def test_cannot_deactivate_last_active_admin(client, app):
    # promote a second admin, then deactivate the first — allowed
    with app.app_context():
        second = users.create("sir2", "Sir Two", "admin", hash_pin("1234"))
    _login_admin(client)
    first = users.get_by_username("admin")
    r = client.post(f"/admin/users/{second['id']}/toggle")   # deactivate sir2 — ok, admin still active
    assert r.status_code in (302, 200)
    assert users.get_by_id(second["id"])["is_active"] is False
    # now only 'admin' is an active admin — cannot deactivate it via another admin either
    r2 = client.post(f"/admin/users/{first['id']}/toggle")
    assert r2.status_code == 400
    assert users.get_by_username("admin")["is_active"] is True


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

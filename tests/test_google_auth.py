"""Employee Google Sign-In: config gate, find-or-create, and route behavior."""
from config import Config


def test_google_oauth_not_configured_by_default():
    assert Config.google_oauth_configured() is False


def test_google_oauth_configured_when_both_set(monkeypatch):
    monkeypatch.setattr(Config, "GOOGLE_OAUTH_CLIENT_ID", "id123")
    monkeypatch.setattr(Config, "GOOGLE_OAUTH_CLIENT_SECRET", "secret123")
    assert Config.google_oauth_configured() is True


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

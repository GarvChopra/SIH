"""Every Admin Portal page renders under the new shell, for an admin only."""
import pytest

PAGES = [
    "/admin", "/admin/grievances", "/admin/recurring", "/admin/pulse",
    "/admin/gaps", "/admin/analytics", "/admin/reports", "/admin/notices",
    "/admin/users", "/admin/locations", "/admin/audit", "/admin/settings",
    "/admin/more",
]


@pytest.fixture()
def admin(client):
    client.post("/login", data={"username": "admin", "pin": "0000"})
    return client


@pytest.mark.parametrize("path", PAGES)
def test_admin_page_renders(admin, path):
    r = admin.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    # served inside the new isolated shell, never the faculty stylesheet alone
    assert b"css/admin.css" in r.data
    assert b'id="axSide"' in r.data


@pytest.mark.parametrize("path", PAGES)
def test_admin_page_blocks_faculty(client, path):
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    assert client.get(path).status_code in (302, 403)


def test_forbidden_page_is_recoverable_not_bare_werkzeug(client):
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    r = client.get("/admin")
    assert r.status_code == 403
    assert b"Admin access required" in r.data
    assert b'href="/"' in r.data and b"Reset app data" in r.data
    # Werkzeug's default body must not be what the user sees
    assert b"read-protected or not readable" not in r.data


def test_forbidden_data_endpoint_stays_json(client):
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    r = client.get("/admin/grievances/data")
    assert r.status_code == 403
    assert r.is_json


def test_dashboard_timeframe_switch(admin):
    for days in (7, 30, 90, 0):
        r = admin.get(f"/admin?range={days}")
        assert r.status_code == 200
    assert b"7 days" in admin.get("/admin").data


def test_settings_pin_change_flow(admin):
    bad = admin.post("/admin/settings/pin",
                     data={"current": "9999", "new": "5678"})
    assert bad.status_code == 302 and "err=" in bad.headers["Location"]
    ok = admin.post("/admin/settings/pin",
                    data={"current": "0000", "new": "5678"})
    assert ok.status_code == 302 and "ok=1" in ok.headers["Location"]
    # new PIN now works
    admin.get("/logout")
    assert admin.post("/login",
                      data={"username": "admin", "pin": "5678"}).status_code == 302


def test_admin_shell_has_no_faculty_bottomnav(admin):
    r = admin.get("/admin")
    assert b'class="bottomnav"' not in r.data
    assert b'class="app-main"' not in r.data      # faculty main wrapper

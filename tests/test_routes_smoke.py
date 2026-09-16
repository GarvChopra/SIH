def test_admin_login_redirects_to_admin(client):
    r = client.post("/login", data={"username": "admin", "pin": "0000"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/admin")


def test_faculty_login_redirects_home(client):
    r = client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    assert r.status_code == 302
    assert r.headers["Location"] == "/"


def test_bad_pin_401(client):
    r = client.post("/login", data={"username": "admin", "pin": "9999"})
    assert r.status_code == 401
    assert b"Incorrect PIN" in r.data


def test_unknown_user_401(client):
    r = client.post("/login", data={"username": "ghost", "pin": "0000"})
    assert r.status_code == 401


def test_logout_clears_and_redirects(client):
    client.post("/login", data={"username": "admin", "pin": "0000"})
    r = client.get("/logout")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/login")


def test_refresh_without_cookie_401(client):
    r = client.post("/auth/refresh")
    assert r.status_code == 401


def test_authenticated_user_in_context(client):
    client.post("/login", data={"username": "admin", "pin": "0000"})
    # login page redirects an authenticated admin to /admin (no /admin route yet -> 404,
    # but the redirect itself proves g.current_user was populated from the cookie)
    r = client.get("/login")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/admin")

def test_admin_dashboard_requires_admin(client):
    r = client.get("/admin")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    assert client.get("/admin").status_code == 403


def test_admin_can_see_dashboard(client):
    client.post("/login", data={"username": "admin", "pin": "0000"})
    r = client.get("/admin")
    assert r.status_code == 200
    assert b"Campus Overview" in r.data
    assert b"Quick actions" in r.data

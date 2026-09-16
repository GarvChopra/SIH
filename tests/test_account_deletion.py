"""Phase 3 — Google Play requires an in-app account-deletion path and a public
page describing it. Reports are de-identified, not hard-deleted."""
from db import audit, grievances, users
from services import grievance_service as gs


def _login(c, u="prof.rao", p="1234"):
    if u == "admin":
        return c.post("/login", data={"username": u, "pin": p})
    return c.post("/auth/google/mock-callback", data={"email": u, "name": u})


def test_public_privacy_and_deletion_pages_render_without_login(client):
    assert client.get("/privacy").status_code == 200
    r = client.get("/account-deletion")
    assert r.status_code == 200 and b"deletion" in r.data.lower()


def test_assetlinks_json_is_served(client, monkeypatch):
    monkeypatch.setenv("TWA_PACKAGE_NAME", "in.ac.glbitm.unifix")
    monkeypatch.setenv("TWA_SHA256_CERT_FINGERPRINTS", "AA:BB, CC:DD")
    r = client.get("/.well-known/assetlinks.json")
    assert r.status_code == 200 and r.mimetype == "application/json"
    j = r.get_json()
    assert j[0]["target"]["package_name"] == "in.ac.glbitm.unifix"
    assert j[0]["target"]["sha256_cert_fingerprints"] == ["AA:BB", "CC:DD"]


def test_profile_has_delete_control(client):
    _login(client)
    assert b"/profile/delete" in client.get("/profile").data


def test_user_can_request_deletion_and_is_logged_out(client):
    _login(client)
    uid = users.get_by_username("prof.rao")["id"]
    r = client.post("/profile/delete", follow_redirects=False)
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    u = users.get_by_id(uid)
    assert u["deletion_requested"] is True and u["is_active"] is False
    assert "user.deletion_requested" in [a["action"] for a in audit.list_recent(20)]
    # session is gone
    assert "/login" in client.get("/", follow_redirects=False).headers["Location"]


def test_admin_completes_deletion_deidentifies_reports(client):
    # a report exists under this user
    c2 = client.application.test_client()
    _login(c2)
    out = c2.post("/report", json={
        "description": "The ceiling fan in this room is completely dead now",
        "location_type": "hostels", "location_label": "Hostels",
        "photo_b64": "aGVsbG8=", "photo_mime": "image/jpeg", "severity": "medium"}).get_json()
    uid = users.get_by_username("prof.rao")["id"]
    users.request_deletion(uid)

    _login(client, "admin", "0000")
    assert b"requested account deletion" in client.get("/admin/users").data
    r = client.post(f"/admin/users/{uid}/delete")
    assert r.status_code == 302
    u = users.get_by_id(uid)
    assert u["display_name"] == "Former staff" and u["contact"] is None
    assert u["pin_hash"] == "" and u["deletion_requested"] is False
    assert grievances.get_by_code(out["code"])["reporter_name"] == "Former staff"


def test_cannot_delete_only_admin(client):
    _login(client, "admin", "0000")
    uid = users.get_by_username("admin")["id"]
    r = client.post(f"/admin/users/{uid}/delete")
    assert r.status_code == 400


def test_second_admin_can_be_deleted(client):
    _login(client, "admin", "0000")
    client.post("/admin/users", data={"username": "admin2", "display_name": "Admin Two",
                                      "role": "admin", "pin": "9999"})
    uid = users.get_by_username("admin2")["id"]
    r = client.post(f"/admin/users/{uid}/delete")
    assert r.status_code == 302
    assert users.get_by_id(uid)["display_name"] == "Former staff"

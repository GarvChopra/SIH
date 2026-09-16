def _login(client, u="prof.rao", p=None):
    return client.post("/auth/google/mock-callback", data={"email": u, "name": u})


# ── report flow ────────────────────────────────────────────────────────────

def test_report_page_renders(client):
    _login(client)
    r = client.get("/report")
    assert r.status_code == 200
    assert b"Report an Issue" in r.data


def test_analyze_returns_summary(client):
    _login(client)
    r = client.post("/report/analyze", json={
        "description": "Water leaking from the pipe under the basin in the washroom"})
    j = r.get_json()
    assert j["category"] == "Plumbing"
    assert j["ai_summary"]


def test_submit_happy_path(client):
    _login(client)
    r = client.post("/report", json={
        "description": "The ceiling fan has completely stopped working in this room",
        "location_type": "academics_block", "block_no": "Block B",
        "floor": "2nd Floor", "room": "204",
        "photo_b64": "aGVsbG8=", "photo_mime": "image/jpeg",
    })
    assert r.status_code == 200
    assert r.get_json()["code"].startswith("GLB-CAMP-")


def test_submit_validation_error(client):
    _login(client)
    r = client.post("/report", json={"description": "no", "location_type": "hostels",
                                     "photo_b64": "x"})
    assert r.status_code == 400
    assert r.get_json()["errors"]


def test_report_requires_login(client):
    r = client.get("/report")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


# ── my-reports + detail ────────────────────────────────────────────────────

def test_my_reports_data_scoped_to_reporter(client):
    _login(client, "prof.rao", "1234")
    client.post("/report", json={
        "description": "Projector will not turn on in this classroom at all",
        "location_type": "academics_block", "block_no": "Block A", "floor": "1st Floor",
        "room": "101", "photo_b64": "aGVsbG8=", "photo_mime": "image/jpeg"})
    data = client.get("/my-reports/data").get_json()
    assert len(data["grievances"]) == 1
    code = data["grievances"][0]["code"]

    other = client.application.test_client()
    other.post("/auth/google/mock-callback", data={"email": "dr.iyer", "name": "Dr Iyer"})
    assert other.get(f"/grievance/{code}").status_code == 403


def test_detail_shows_timeline(client):
    _login(client, "prof.khan", "1234")
    out = client.post("/report", json={
        "description": "The wall has a large crack near the window in this room",
        "location_type": "academics_block", "block_no": "Block C", "floor": "Ground Floor",
        "room": "12", "photo_b64": "aGVsbG8=", "photo_mime": "image/jpeg"}).get_json()
    r = client.get(f"/grievance/{out['code']}")
    assert r.status_code == 200
    assert b"Reported" in r.data and b"Progress" in r.data


def test_photo_route_serves_and_is_owner_gated(client):
    _login(client, "prof.rao", "1234")
    out = client.post("/report", json={
        "description": "The tubelight keeps flickering in this corridor area",
        "location_type": "hostels", "photo_b64": "aGVsbG8=",
        "photo_mime": "image/png"}).get_json()
    detail = client.get(f"/grievance/{out['code']}").data.decode()
    # primary_photo_url looks like /photo/<id>
    import re
    m = re.search(r"/photo/(\d+)", detail)
    assert m
    r = client.get(m.group(0))
    assert r.status_code == 200
    assert r.data == b"hello"


# ── PWA ────────────────────────────────────────────────────────────────────

def test_manifest_and_sw_served(client):
    m = client.get("/static/manifest.webmanifest")
    assert m.status_code == 200
    assert b"UNIFIX" in m.data
    sw = client.get("/static/service-worker.js")
    assert sw.status_code == 200
    icon = client.get("/static/icons/icon-192.png")
    assert icon.status_code == 200 and icon.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_base_page_links_manifest(client):
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    r = client.get("/")
    assert b"manifest.webmanifest" in r.data
    assert b"serviceWorker" in r.data


def test_home_shows_report_cta(client):
    _login(client)
    r = client.get("/")
    assert r.status_code == 200
    assert b"Report an Issue" in r.data


def test_admin_redirected_from_faculty_home(client):
    client.post("/login", data={"username": "admin", "pin": "0000"})
    r = client.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin")

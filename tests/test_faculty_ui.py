def _login(c, u="prof.rao", **extra):
    return c.post("/auth/google/mock-callback", data={"email": u, "name": u, **extra})


def test_profile_page_renders(client):
    _login(client)
    assert client.get("/profile").status_code == 200


def test_home_shows_campus_health(client):
    _login(client)
    r = client.get("/")
    assert b"Campus Health" in r.data


def test_remember_me_sets_long_cookie(client):
    r = _login(client, remember="1")
    ref = [c for c in r.headers.getlist("Set-Cookie") if c.startswith("up_refresh=")][0]
    assert "Max-Age=2592000" in ref


def test_profile_reachable_from_header(client):
    _login(client)
    r = client.get("/")
    assert b'href="/profile"' in r.data          # avatar in the header
    assert r.data.count(b'class="bottomnav"') == 1
    assert b">My Reports</a>" in r.data and b">Notices</a>" in r.data


def test_form_category_overrides_ai(client):
    _login(client)
    r = client.post("/report", json={
        "description": "Water is dripping from the ceiling near the light fitting",
        "location_type": "academics_block", "block_no": "Block B", "floor": "2nd Floor",
        "room": "204", "photo_b64": "aGVsbG8=", "photo_mime": "image/jpeg",
        "severity": "high", "category": "Civil"})
    from db import grievances
    assert grievances.get_by_code(r.get_json()["code"])["category"] == "Civil"


def test_report_form_has_category_and_review_step(client):
    _login(client)
    html = client.get("/report").data
    assert b'id="cat"' in html
    assert b">Review<" in html and b"Use Photo" in html


def test_submit_with_form_priority_and_fields(client):
    _login(client)
    r = client.post("/report", json={
        "description": "The tube light in this room is flickering badly since two days",
        "location_type": "academics_block", "block_no": "Block B", "floor": "2nd Floor",
        "room": "204", "photo_b64": "aGVsbG8=", "photo_mime": "image/jpeg",
        "severity": "high", "affects_academics": True, "noticed_at": 1724990000.0})
    assert r.status_code == 200
    from db import grievances
    g = grievances.get_by_code(r.get_json()["code"])
    assert g["severity"] == "high"
    assert g["affects_academics"] is True
    assert g["noticed_at"] == 1724990000.0


def test_report_page_has_stepper_and_camera(client):
    _login(client)
    html = client.get("/report").data
    assert b"stepper" in html and b'id="startcam"' in html
    assert b"Report an Issue" in html


def test_my_reports_data_has_title_and_severity(client):
    _login(client)
    client.post("/report", json={
        "description": "Projector will not turn on in this classroom at all",
        "location_type": "academics_block", "block_no": "Block A", "floor": "1st Floor",
        "room": "101", "photo_b64": "aGVsbG8=", "photo_mime": "image/jpeg",
        "severity": "medium"})
    row = client.get("/my-reports/data").get_json()["grievances"][0]
    assert "title" in row and row["severity"] == "medium"

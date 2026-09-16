"""Admin dashboard composition (services/dashboard_service.py)."""
from services import dashboard_service as ds

_KEYS = ("range_days", "range_label", "ranges", "kpis", "employees",
         "overdue_rows", "recurring_rows", "quick_actions", "generated_at")


def _report(client, user="prof.rao",
            desc="the ceiling fan is not working in this room again"):
    c = client.application.test_client()
    c.post("/auth/google/mock-callback", data={"email": user, "name": user})
    return c.post("/report", json={
        "description": desc, "location_type": "academics_block", "block_no": "AB1",
        "floor": "2nd Floor", "room": "204", "photo_b64": "aGVsbG8=",
        "photo_mime": "image/jpeg"}).get_json()


def test_resolve_range():
    assert ds.resolve_range("7") == 7
    assert ds.resolve_range("90") == 90
    assert ds.resolve_range("0") == 0
    assert ds.resolve_range(None) == 30
    assert ds.resolve_range("nonsense") == 30
    assert ds.resolve_range("999") == 30


def test_overview_shape_on_empty_store(app):
    o = ds.overview(30)
    for k in _KEYS:
        assert k in o, f"missing {k}"
    assert o["range_days"] == 30
    assert len(o["kpis"]) == 6
    assert all({"label", "value", "icon"} <= set(k) for k in o["kpis"])
    assert o["kpis"][0]["value"] == 0
    assert o["employees"]["admins"] >= 1          # the seeded dev admin
    assert o["overdue_rows"] == []
    assert len(o["quick_actions"]) >= 3


def test_overview_reflects_reports(client):
    _report(client)
    _report(client, user="dr.iyer")
    o = ds.overview(30)
    assert o["kpis"][0]["value"] == 2            # total issues
    assert o["kpis"][0]["sub"].startswith("2 new")
    # two reports, same room + category -> recurring group
    assert o["kpis"][5]["value"] == 1
    assert o["recurring_rows"] and o["recurring_rows"][0]["reports"] == 2


def test_alerts_cache_and_force(client):
    first = ds.alerts(force=True)
    assert isinstance(first, list)
    _report(client)                              # changes underlying data
    assert ds.alerts() is not None              # cached call still safe
    forced = ds.alerts(force=True)
    assert isinstance(forced, list)


def test_activity_feed_lists_admin_actions(client):
    client.post("/login", data={"username": "admin", "pin": "0000"})
    client.post("/admin/users", data={
        "username": "new.prof", "display_name": "New Prof",
        "pin": "4321", "role": "reporter"})
    feed = ds.activity(5)
    assert feed
    assert "new.prof" in feed[0]["text"]
    assert feed[0]["icon"] and feed[0]["time"]


def test_humanise_escapes_hostile_input():
    row = {"actor": "<script>x</script>", "action": "location.create",
           "detail": {"path": "<img src=x onerror=1>"}, "created_at": None}
    text = ds._humanise(row)["text"]
    assert "<script" not in text and "<img" not in text
    assert "&lt;script&gt;" in text

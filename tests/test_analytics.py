import time

from db import grievances, users
from services import intelligence_service as si
from services.auth_service import hash_pin


def _g(memstore, **kw):
    u = users.create(f"u{time.time_ns()}", "U", "reporter", hash_pin("1"))
    base = dict(reporter_id=u["id"], reporter_name="U", title="t",
                description="broken thing here now", location_type="hostels",
                location_label="Hostels", category="Electric", severity="medium",
                status="reported", priority_score=10, created_at=time.time())
    base.update(kw)
    return grievances.insert(**base)


def test_analytics_empty(memstore):
    a = si.analytics()
    assert a["total"] == 0
    assert a["resolution_rate"] == 0.0
    assert a["avg_resolution_hours"] is None
    assert set(a["by_category"]) == set(si.CATEGORIES)


def test_analytics_rates(memstore):
    _g(memstore, status="reported")
    r = _g(memstore, status="closed")
    grievances.update(r["id"], created_at=time.time() - 10 * 3600,
                      resolved_at=time.time() - 4 * 3600)   # 6h to resolve
    b = _g(memstore, status="assigned")
    grievances.update(b["id"], due_at=time.time() - 3600)
    a = si.analytics()
    assert a["total"] == 3
    assert a["resolution_rate"] == round(1 / 3 * 100, 1)
    assert a["sla_breach_rate"] == round(1 / 3 * 100, 1)
    assert a["avg_resolution_hours"] == 6.0
    assert a["by_category"]["Electric"] == 3
    assert a["by_status"]["closed"] == 1


def test_analytics_by_unit(memstore):
    _g(memstore, status="resolved", responsible_unit="Infrastructure")
    _g(memstore, status="assigned", responsible_unit="Infrastructure")
    a = si.analytics()
    assert a["by_unit"]["Infrastructure"] == {"total": 2, "resolved": 1}


def test_analytics_routes(client):
    client.post("/login", data={"username": "admin", "pin": "0000"})
    assert client.get("/admin/analytics").status_code == 200
    csv_r = client.get("/admin/analytics.csv")
    assert csv_r.status_code == 200
    assert csv_r.mimetype == "text/csv"
    assert b"code,category,severity" in csv_r.data


def test_analytics_blocked_for_faculty(client):
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    assert client.get("/admin/analytics").status_code == 403
    assert client.get("/admin/analytics.csv").status_code == 403

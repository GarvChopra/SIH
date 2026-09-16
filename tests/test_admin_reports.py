"""Admin Reports & Downloads page — time-windowed issue export (7 / 30 / 60 days / all)."""
import csv
import io
import time

from db import grievances as gdb


def _admin(client):
    client.post("/login", data={"username": "admin", "pin": "0000"})


def _issue(age_days: float):
    g = gdb.insert(reporter_id=1, reporter_name="Prof X", title="t",
                   description="something is broken in here",
                   location_type="hostels", location_label="Hostels",
                   category="Electric", severity="medium")
    gdb.update(g["id"], created_at=time.time() - age_days * 86400)
    return g


def test_reports_page_defaults_to_last_7_days(client):
    _issue(2)
    _issue(20)
    _admin(client)
    r = client.get("/admin/reports")
    assert r.status_code == 200
    assert b"last 7 days" in r.data.lower()


def test_window_filtering(client):
    _issue(2)      # in every window
    _issue(20)     # 30d, 60d, all
    _issue(45)     # 60d, all
    _issue(200)    # all only
    _admin(client)

    def n(window):
        return len(client.get(f"/admin/reports.csv?window={window}").data
                   .decode().strip().splitlines()) - 1  # minus header row

    assert n("7d") == 1
    assert n("30d") == 2
    assert n("60d") == 3
    assert n("all") == 4


def test_window_boundary(client):
    _issue(6.9)    # inside the 7-day window
    _issue(8)      # just outside it
    _admin(client)
    body = client.get("/admin/reports.csv?window=7d").data.decode()
    assert len(body.strip().splitlines()) - 1 == 1


def test_csv_download_headers(client):
    _issue(1)
    _admin(client)
    r = client.get("/admin/reports.csv?window=30d")
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    assert "attachment" in r.headers["Content-Disposition"]
    assert "30" in r.headers["Content-Disposition"]
    reader = list(csv.reader(io.StringIO(r.data.decode())))
    assert reader[0][0] == "Code"
    assert "Reported On" in reader[0]
    assert len(reader) == 2


def test_invalid_window_falls_back_to_7d(client):
    _issue(2)
    _issue(20)
    _admin(client)
    body = client.get("/admin/reports.csv?window=banana").data.decode()
    assert len(body.strip().splitlines()) - 1 == 1


def test_reports_blocked_for_non_admin(client):
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    assert client.get("/admin/reports").status_code == 403
    assert client.get("/admin/reports.csv").status_code == 403


def test_reports_page_lists_issue_codes(client):
    g = _issue(1)
    _admin(client)
    r = client.get("/admin/reports?window=7d")
    assert g["code"].encode() in r.data

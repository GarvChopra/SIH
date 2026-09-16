def test_offline_page_no_login(client):
    r = client.get("/offline")
    assert r.status_code == 200
    assert b"offline" in r.data.lower()


def test_service_worker_has_offline_fallback(client):
    sw = client.get("/static/service-worker.js").data.decode()
    assert "/offline" in sw
    assert 'req.mode === "navigate"' in sw


def test_service_worker_served_at_root_with_scope_header(client):
    # must be reachable from "/" so its scope covers the whole app, otherwise
    # Chrome never fires beforeinstallprompt on the app pages
    r = client.get("/service-worker.js")
    assert r.status_code == 200
    assert r.headers.get("Service-Worker-Allowed") == "/"
    assert b"self.addEventListener" in r.data


def test_login_page_has_install_nudge(client):
    r = client.get("/login")
    assert r.status_code == 200
    # once-per-device popup + a persistent banner, both routing to /get-app
    assert b'id="pwa-install-modal"' in r.data
    assert b"Install the UNIFIX app" in r.data
    assert b'href="/get-app"' in r.data


def test_get_app_page_is_public_and_has_install_action(client):
    r = client.get("/get-app")
    assert r.status_code == 200
    assert b"beforeinstallprompt" in r.data
    assert b"Android app coming soon" in r.data
    assert b"instant download" in r.data.lower()


def test_home_page_has_no_floating_install_button(client):
    client.post("/auth/google/mock-callback", data={"email": "prof.rao", "name": "Prof Rao"})
    r = client.get("/")
    assert b'id="pwa-install"' not in r.data

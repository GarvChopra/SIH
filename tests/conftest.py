import os

import pytest

# Keep the suite offline, in development mode, on the in-memory store, with no AI
# key. config.py calls load_dotenv(override=False) on import, so any real
# credential left unset here would be pulled in from .env and make tests hit live
# services. Setting each to "" (not pop) blocks that.
os.environ["APP_ENV"] = "development"
for _k in ("DATABASE_URL", "COHERE_API_KEY", "INITIAL_ADMIN_USERNAME", "INITIAL_ADMIN_PIN",
           "ALLOW_PROD_DEMO_SEED", "FLASK_DEBUG", "FIREBASE_KEY_JSON",
           "FIREBASE_CREDENTIALS_B64", "FIREBASE_SERVICE_ACCOUNT_JSON",
           "GOOGLE_APPLICATION_CREDENTIALS"):
    os.environ[_k] = ""
# tests start from an empty case register; the dataset has its own tests
os.environ["SEED_DEMO_DATA"] = "0"
os.environ["BCRYPT_ROUNDS"] = "4"      # fast hashing in tests


@pytest.fixture(autouse=True)
def _reset_process_state(tmp_path, monkeypatch):
    from db import pool
    from services import auth_service, file_service
    auth_service._ATTEMPTS.clear()
    monkeypatch.setattr(file_service, "UPLOAD_DIR", str(tmp_path / "uploads"))
    yield
    auth_service._ATTEMPTS.clear()
    pool.reset_memory_store()


@pytest.fixture()
def memstore():
    """Fresh in-memory store (bureaus + demo accounts seeded), no web app."""
    from db import pool, seeds
    pool.reset_memory_store()
    seeds.seed_essential()
    seeds.seed_demo_accounts()
    seeds.seed_demo_citizen()
    return pool.STATE


@pytest.fixture()
def app():
    from app import create_app
    from db import pool
    pool.reset_memory_store()
    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, username, pin="1234"):
    r = client.post("/login", data={"username": username, "pin": pin})
    assert r.status_code == 302, r.data[:300]
    return client


@pytest.fixture()
def login_as(app):
    """login_as('pgrc.officer') -> a fresh logged-in test client."""
    def _make(username, pin="1234"):
        c = app.test_client()
        login(c, username, pin)
        return c
    return _make


@pytest.fixture()
def actor(memstore):
    """actor('rb.admin') -> the session-user dict the services expect."""
    from db import users

    def _get(username):
        u = users.get_by_username(username)
        return {"id": u["id"], "username": u["username"], "display_name": u["display_name"],
                "role": u["role"], "bureau_id": u["bureau_id"], "region": u["region"],
                "department": u["department"]}
    return _get


@pytest.fixture()
def inst(memstore):
    """Two institutions; returns the ABC Engineering College row."""
    from db import institutions
    abc = institutions.create("123456", "ABC Engineering College", "Greater Noida", "North")
    institutions.create("654321", "Southern Institute of Technology", "Chennai", "South")
    return abc

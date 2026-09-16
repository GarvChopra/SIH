import os

import pytest

# Keep the test suite fully offline, in development mode, and on the in-memory
# store. config.py calls load_dotenv(override=False) on import, so any real
# credential left unset here would be pulled in from .env and make tests hit
# live services (Neon, Groq, Resend). Setting each to "" (not pop) blocks that —
# load_dotenv won't override a key that is already present, even if it is empty.
os.environ["APP_ENV"] = "development"
for _k in ("DATABASE_URL", "GROQ_API_KEY", "RESEND_API_KEY", "IMAGEKIT_PRIVATE_KEY",
           "FIREBASE_KEY_JSON", "FIREBASE_CREDENTIALS_B64",
           "FIREBASE_SERVICE_ACCOUNT_JSON", "GOOGLE_APPLICATION_CREDENTIALS",
           "R2_ACCOUNT_ID", "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_BUCKET_NAME",
           "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "S3_BUCKET_NAME",
           "INITIAL_ADMIN_USERNAME", "INITIAL_ADMIN_PIN",
           "ALLOW_PROD_DEMO_SEED", "SEED_DEMO", "FLASK_DEBUG",
           "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"):
    os.environ[_k] = ""
# Pinned (not blanked) so the college-domain restriction tests are deterministic
# regardless of what a real .env sets.
os.environ["GOOGLE_ALLOWED_EMAIL_DOMAIN"] = "glbitm.ac.in"


@pytest.fixture(autouse=True)
def _reset_process_state():
    """Clear module-level caches that would otherwise leak between tests."""
    from db import pool
    from services import auth_service, dashboard_service
    auth_service._ATTEMPTS.clear()
    dashboard_service._ALERT_CACHE.update(t=0.0, v=[])
    yield
    auth_service._ATTEMPTS.clear()
    dashboard_service._ALERT_CACHE.update(t=0.0, v=[])
    pool.reset_memory_store()


@pytest.fixture()
def memstore():
    """Fresh in-memory persistence store — for db-layer tests that don't need the web app."""
    from db import pool
    pool.reset_memory_store()
    return pool.STATE


@pytest.fixture()
def app():
    from app import create_app
    from db import pool
    pool.reset_memory_store()          # fresh dict store per test
    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()

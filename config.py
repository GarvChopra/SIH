"""Environment configuration. One object, read once at startup.

Security model
--------------
Development / test keep working defaults so the app runs with an empty
environment. Production (`APP_ENV=production`) refuses to start unless the
security-sensitive values are set to real secrets — see `Config.validate()`,
which `create_app()` calls before anything else.
"""
import logging
import os

log = logging.getLogger("unifix.config")

# Load .env before any os.environ read below. Without this, DATABASE_URL (and the
# other keys) are invisible unless the host injects them, so the app silently
# falls back to the in-memory store — which is NOT shared between gunicorn
# workers, so faculty reports written by one worker never appear on the admin
# side served by the other. override=False so real host env vars still win.
try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:  # python-dotenv not installed — rely on host env vars
    pass


# Dev-only fallbacks. These are PUBLIC values — anything using them can be
# forged, so `validate()` blocks them in production.
_DEV_SECRET_KEY = "unifix-dev-secret-not-for-production"
_DEV_JWT_SECRET = "unifix-jwt-dev-secret-not-for-production"
_MIN_SECRET_LEN = 32


class Config:
    DATABASE_URL         = os.environ.get("DATABASE_URL", "").strip()
    SECRET_KEY           = os.environ.get("SECRET_KEY", "").strip() or _DEV_SECRET_KEY
    JWT_SECRET           = os.environ.get("JWT_SECRET", "").strip() or _DEV_JWT_SECRET
    APP_ENV              = os.environ.get("APP_ENV", os.environ.get("FLASK_ENV", "development")).lower()
    GROQ_API_KEY         = os.environ.get("GROQ_API_KEY", "").strip()
    RESEND_API_KEY       = os.environ.get("RESEND_API_KEY", "").strip()
    IMAGEKIT_PRIVATE_KEY = os.environ.get("IMAGEKIT_PRIVATE_KEY", "").strip()
    GOOGLE_OAUTH_CLIENT_ID     = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    # Groq: qwen3.6-27b is multimodal (text + image), so one model covers both paths.
    GROQ_MODEL_TEXT   = os.environ.get("GROQ_MODEL_TEXT", "qwen/qwen3.6-27b").strip()
    GROQ_MODEL_VISION = os.environ.get("GROQ_MODEL_VISION", "qwen/qwen3.6-27b").strip()
    RESEND_FROM       = os.environ.get("RESEND_FROM", "UNIFIX <onboarding@resend.dev>").strip()
    ADMIN_ALERT_EMAIL = (os.environ.get("ADMIN_ALERT_EMAIL")
                         or os.environ.get("DEMO_RECIPIENT_EMAIL") or "").strip()

    # Initial admin (production): the app will not auto-create admin/0000 in
    # production. Set these to bootstrap the first coordinator account instead.
    INITIAL_ADMIN_USERNAME = os.environ.get("INITIAL_ADMIN_USERNAME", "").strip()
    INITIAL_ADMIN_PIN      = os.environ.get("INITIAL_ADMIN_PIN", "").strip()

    # Unmistakable override: allow demo/sample data to be seeded into a
    # production database. Default off.
    ALLOW_PROD_DEMO_SEED = os.environ.get("ALLOW_PROD_DEMO_SEED", "").strip() == "1"

    @classmethod
    def is_production(cls) -> bool:
        return cls.APP_ENV == "production"

    @classmethod
    def google_oauth_configured(cls) -> bool:
        return bool(cls.GOOGLE_OAUTH_CLIENT_ID and cls.GOOGLE_OAUTH_CLIENT_SECRET)

    @classmethod
    def db_is_remote(cls) -> bool:
        dsn = cls.DATABASE_URL
        if not dsn:
            return False
        host = dsn.split("@", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
        return host not in ("", "localhost", "127.0.0.1", "::1", "host.docker.internal")

    @classmethod
    def _secret_problems(cls) -> list[str]:
        problems: list[str] = []
        for name, value, dev_default in (
            ("SECRET_KEY", cls.SECRET_KEY, _DEV_SECRET_KEY),
            ("JWT_SECRET", cls.JWT_SECRET, _DEV_JWT_SECRET),
        ):
            if not value:
                problems.append(f"{name} is not set")
            elif value == dev_default:
                problems.append(f"{name} is still the built-in development value")
            elif len(value) < _MIN_SECRET_LEN:
                problems.append(f"{name} is too short (need >= {_MIN_SECRET_LEN} chars)")
        return problems

    @classmethod
    def validate(cls) -> None:
        """Called once at startup. Raises RuntimeError in production if the
        environment is unsafe; logs warnings in development."""
        problems = cls._secret_problems()

        if cls.is_production():
            if not cls.DATABASE_URL:
                problems.append("DATABASE_URL is not set (required in production)")
            if problems:
                raise RuntimeError(
                    "UNIFIX refusing to start in production — fix the environment:\n  - "
                    + "\n  - ".join(problems)
                )
            log.info("config validated — env=production")
        else:
            for p in problems:
                log.warning("dev config: %s (would abort startup in production)", p)
            if cls.db_is_remote():
                log.warning(
                    "APP_ENV=%s but DATABASE_URL points at a REMOTE host — every "
                    "local run reads/writes that shared database with insecure "
                    "(non-Secure) cookies and dev-only seeding. Use a local or "
                    "throwaway database for development.", cls.APP_ENV)

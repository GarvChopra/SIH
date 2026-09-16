"""Employee Google Sign-In: config gate, find-or-create, and route behavior."""
from config import Config


def test_google_oauth_not_configured_by_default():
    assert Config.google_oauth_configured() is False


def test_google_oauth_configured_when_both_set(monkeypatch):
    monkeypatch.setattr(Config, "GOOGLE_OAUTH_CLIENT_ID", "id123")
    monkeypatch.setattr(Config, "GOOGLE_OAUTH_CLIENT_SECRET", "secret123")
    assert Config.google_oauth_configured() is True

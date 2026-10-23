from flask import Flask


def test_gunicorn_app_colon_app_resolves_lazily_and_wsgi_works():
    """Render's default start command is `gunicorn app:app`."""
    import importlib
    import sys
    sys.modules.pop("app", None)
    mod = importlib.import_module("app")
    assert mod._APP is None                       # importing for create_app() creates nothing
    assert isinstance(mod.app, Flask) and mod.app is mod.app
    import wsgi
    assert isinstance(wsgi.app, Flask)
    try:
        mod.nonexistent
    except AttributeError:
        pass
    else:
        raise AssertionError("unknown attributes must still raise")

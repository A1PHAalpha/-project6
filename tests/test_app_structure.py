"""Structural checks for the Web application composition."""
from webapp.app import app, create_app
from webapp.main import app as entrypoint_app


def test_app_factory_keeps_public_routes():
    factory_app = create_app()
    paths = {route.path for route in factory_app.routes}

    assert factory_app is not app
    assert entrypoint_app is app
    assert "/" in paths
    assert "/legacy" in paths
    assert "/login" in paths
    assert "/api/diagnose" in paths
    assert "/api/v1/overview" in paths

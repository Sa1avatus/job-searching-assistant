"""Every POST route of the browser worker must take its request as a JSON body.

The module uses ``from __future__ import annotations``; a request model defined below its route
is unresolved when the decorator runs and FastAPI silently degrades the parameter to a required
query parameter, so every real call is rejected with 422.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.workers.browser_http import app


def test_post_routes_accept_request_body_not_query() -> None:
    post_routes = [
        route for route in app.routes if isinstance(route, APIRoute) and "POST" in route.methods
    ]
    assert post_routes
    for route in post_routes:
        assert route.body_field is not None, route.path
        assert not route.dependant.query_params, route.path

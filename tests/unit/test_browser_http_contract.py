"""Every POST route of the browser worker must take its request as a JSON body.

The module uses ``from __future__ import annotations``; a request model defined below its route
is unresolved when the decorator runs and FastAPI silently degrades the parameter to a required
query parameter, so every real call is rejected with 422.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.workers.browser_http import CustomSearchRequest, app


def test_post_routes_accept_request_body_not_query() -> None:
    post_routes = [
        route for route in app.routes if isinstance(route, APIRoute) and "POST" in route.methods
    ]
    assert post_routes
    for route in post_routes:
        assert route.body_field is not None, route.path
        assert not route.dependant.query_params, route.path


def test_custom_search_request_accepts_a_recipe_with_recorded_reach_steps() -> None:
    """A recipe built from a recorded scenario nests dicts/lists (reach_steps) under string
    keys; a ``dict[str, str]`` annotation here silently rejects every such recipe with a 422
    before the request even reaches the handler.
    """
    request = CustomSearchRequest(
        user_id="user-1",
        site={"site_key": "example", "allowed_hosts": ["example.com"]},
        recipe={
            "url_template": "",
            "card_selector": "li",
            "reach_steps": [
                {
                    "action_type": "navigate",
                    "parameters": {"url": "https://example.com"},
                    "timeout_ms": 10000,
                    "is_enabled": True,
                }
            ],
        },
        search_text="python developer",
    )

    assert request.recipe["reach_steps"] == [
        {
            "action_type": "navigate",
            "parameters": {"url": "https://example.com"},
            "timeout_ms": 10000,
            "is_enabled": True,
        }
    ]

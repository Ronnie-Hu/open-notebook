"""Account-mode API requests must use server-verified identity and CSRF."""

from uuid import UUID

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from api.account_auth import TagentAccountMiddleware
from open_notebook.database.tenant_access import current_account, current_role

ACCOUNT_ID = UUID("db9f0e0c-4d8e-4a84-8b9a-c7576450212d")
COOKIE = "__Host-tagent_session=opaque-session"
ORIGIN = "https://tagent.example.org"


def _app(transport: httpx.MockTransport, provisioner=None) -> TagentAccountMiddleware:
    async def item(_request):
        return JSONResponse({"account": str(current_account()), "role": current_role()})

    async def provision(_account_id):
        return None

    return TagentAccountMiddleware(
        Starlette(
            routes=[
                Route("/item", item, methods=["GET", "POST"]),
                Route("/health", item),
            ]
        ),
        verifier_url="http://127.0.0.1:5001",
        cookie_name="__Host-tagent_session",
        public_origin=ORIGIN,
        provisioner=provisioner or provision,
        transport=transport,
    )


def _verifier(request: httpx.Request) -> httpx.Response:
    assert request.url == "http://127.0.0.1:5001/auth/me"
    assert request.headers["cookie"] == COOKIE
    assert "x-tagent-account-id" not in request.headers
    return httpx.Response(
        200,
        json={
            "user": {
                "id": str(ACCOUNT_ID),
                "role": "student",
                "active": True,
                "deletion_pending": False,
            },
            "csrf": "real-csrf",
        },
    )


@pytest.mark.asyncio
async def test_verified_account_is_bound_for_only_this_request() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(httpx.MockTransport(_verifier))),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/item",
            headers={"cookie": COOKIE, "x-tagent-account-id": "forged"},
        )
        assert response.status_code == 200
        assert response.json() == {"account": str(ACCOUNT_ID), "role": "student"}
        assert current_account() is None
        assert (await client.get("/item")).status_code == 401
        assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_writes_require_exact_origin_and_csrf() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(httpx.MockTransport(_verifier))),
        base_url="http://testserver",
    ) as client:
        for headers in (
            {"cookie": COOKIE},
            {
                "cookie": COOKIE,
                "origin": "https://other.example.org",
                "x-tagent-csrf": "real-csrf",
            },
            {"cookie": COOKIE, "origin": ORIGIN, "x-tagent-csrf": "wrong"},
        ):
            assert (await client.post("/item", headers=headers)).status_code == 403
        allowed = await client.post(
            "/item",
            headers={"cookie": COOKIE, "origin": ORIGIN, "x-tagent-csrf": "real-csrf"},
        )
        assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_duplicate_cookies_and_unavailable_identity_fail_closed() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(httpx.MockTransport(_verifier))),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/item", headers={"cookie": COOKIE + "; " + COOKIE})
        assert response.status_code == 401

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=_app(httpx.MockTransport(lambda _request: httpx.Response(503)))
        ),
        base_url="http://testserver",
    ) as client:
        assert (
            await client.get("/item", headers={"cookie": COOKIE})
        ).status_code == 503


@pytest.mark.asyncio
async def test_account_provision_failure_returns_503() -> None:
    async def unavailable(_account_id):
        raise RuntimeError("database unavailable")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=_app(httpx.MockTransport(_verifier), provisioner=unavailable)
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/item", headers={"cookie": COOKIE})
        assert response.status_code == 503
        assert "database unavailable" not in response.text


def test_middleware_requires_secure_origin_and_loopback_verifier() -> None:
    for origin, verifier in (
        ("http://tagent.example.org", "http://127.0.0.1:5001"),
        (ORIGIN, "https://remote.example.org"),
    ):
        with pytest.raises(ValueError):
            TagentAccountMiddleware(
                Starlette(),
                verifier_url=verifier,
                cookie_name="__Host-tagent_session",
                public_origin=origin,
            )
